# File I/O — a write that can fail is destructive on the only copy

Extends `craft-code`.

## `open(path, 'w')` truncates BEFORE the write can fail

**What broke** — a throwaway patch script did
`io.open(p, 'w', encoding='utf-8').write(s)`. The string contained a lone
surrogate, so the write raised `UnicodeEncodeError` — but `open(path, 'w')` had
already truncated the target at open time. The file was left at zero bytes.
This happened twice; the second time the victim was a 56-test module that was
untracked, so git could not restore it and it had to be rewritten from
scratch.

**Why** — `open(path, 'w')` truncates the moment the file handle opens, not
when the write succeeds. Anything that can raise partway through a write —
an encoding error, a serialization error, a crash — has already destroyed the
previous contents.

**The rule** — never write the sole copy of a file with a bare
`open(path, 'w')` in a script that can fail mid-write. Either:

- write to a temp file in the same directory and `os.replace()` it into place
  once the write fully succeeds, or
- encode first (`data = s.encode('utf-8')`) so an encoding error raises
  **before** anything is truncated, then write the bytes.

**Corollary that caused it** — never put a literal lone surrogate
(`'\ud800'`–`'\udfff'`) in a script's own source. A file containing one cannot
be saved as valid UTF-8 text. Build it at runtime instead — `chr(0xD800)` — and
pass it through `json.dumps` rather than embedding the escape literally.

**Where it bit** — acme-audit, a patch script and a test module, both
untracked and unrecoverable from git.

## A multi-line pattern silently matches NOTHING on a CRLF file

A scripted edit that searches for `"def foo():
    bar"` finds nothing in a
file whose line endings are `
`. There is no error: the replace runs, the
count is zero, the file is written back unchanged, and the script reports
success. Single-line patterns keep matching, so the failure looks arbitrary —
some edits land, some do not.

> Measured across one session on a Windows repo: four scripted edits in a row
> "succeeded" while changing nothing, including one that was supposed to pass a
> parameter through a view. That one shipped as a real defect — the value was
> computed, authorized, and then silently not used — and a test asserting the
> property passed at a different layer.

The tell is a `replace()` whose result you did not verify, or an `assert
old in s` that fires on a file you can plainly see contains the text.

**The rule:** read with universal newlines and write back explicitly.

```python
s = io.open(p, encoding='utf-8').read()          # 
 -> 
 on read
...                                              # patterns use 

io.open(p, 'w', encoding='utf-8', newline='
').write(s)
```

Reading with `newline=''` preserves the CRLF and is what breaks the patterns —
use it only when you genuinely need the bytes unchanged.

**And verify the edit landed, every time**: assert the pattern was found, or
grep the result afterwards. A scripted edit that reports success without
proving it changed anything is the same silent-success class as a check that
cannot fail.
