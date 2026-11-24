# Consultation Email Pre-processing

This script prepares QC emails for case-intake experiments. QC messages are the
only source for cleaned email bodies and attachments; other messages in the
input folder are not used as fallback sources.

## Why QC Emails

The source folders contain multiple messages per consultation, often with
deeply nested email attachments. The original consultation request can be hard
to distinguish from later QC, response, and filing messages. For this PoC, QC
emails are the chosen source because review of the sample data found they are
often shallower in the chain, commonly carry the consultation document needed
for intake, and may quote the original request. The script extracts that
original message rather than using later QC or response text.

Run it from the repository root:

```sh
uv run python pre_processing/clean_consultation_emails.py [input_dir output_dir]
```

Without arguments, the script reads `data/temp/unnecessary` and writes to
`data/temp/unnecessary_clean`. Pass both directories to use another dataset.

## Decisions

- Only QC emails are processed. The script identifies them from their filename,
  subject, or recognizable QC-completion wording at the start of the body. It
  does not copy bodies or attachments from other emails in the folder.
- Attachments are recursively collected from QC emails, including documents
  inside nested attached emails. Nested email bodies are included as separate
  QC messages only when the nested message itself matches the QC identification
  rules.
- QC emails are grouped across the entire input root by the six-digit
  consultation ID found in the filename, subject, or body. Each ID produces
  one output, even when its QC emails are stored in different source folders.
  If no ID can be found, the input folder name is used as the group ID.
- The body is extracted from the original/root email in each QC thread. The
  script recognizes a plain-text forwarded chain with `From` and `Sent`/`Date`,
  recipient, or subject headers, then keeps only the text after the last such
  header block. This omits the QC discussion above it. Distinct root bodies are
  joined once; exact repeats after whitespace normalization are included once.
- The cleaned email uses headers from the first recognized root message. If no
  root body is recognized, the body is left empty rather than falling back to a
  non-QC email. A neutral consultation subject is used if no root subject is
  available.
- Eligible attachments are gathered only from QC emails in the group. The
  existing extension filter keeps `.pdf`, `.doc`, `.docx`, and `.rtf`; filenames
  containing `checklist`, `response`, or `annex` are excluded. Byte-identical
  attachments are kept once, even if their filenames differ. Different file
  contents are retained even when names match. These exclusions reduce the
  risk of response or reviewer-checklist information leaking into decision
  experiments; filtering is filename-based and does not inspect document text.
- `.eml` and `.msg` messages are supported using the project's email parser.

These are conservative PoC rules, not a general email-thread parser. The script
does not infer semantic equivalence, select a winner between different document
versions, or guarantee recognition of HTML-only or unusually formatted email
chains. When a QC message has no recognized root boundary, its body is omitted;
review the log and source email if that occurs. Filename filtering cannot catch
documents whose names conceal response or checklist content, so outputs remain
experimental data that should be reviewed before use.

## Output

Each retained cleaned email is written as
`<output_dir>/<consultation_id>/<consultation_id> clean.eml`.
`preprocessing_log.csv` records status, source QC emails, kept and removed
attachment filenames, and any note. Statuses are `ok`, `skipped`, and `error`.
`skipped` means no QC email was found or no eligible attachment was available.