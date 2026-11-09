Document screening: to fix

1. Any file type other than PDF or image stops the whole run. 
.doc
, 
.docx
 or .tif files get passed straight to the model client, which rejects them for Ollama. On file types, here's what the four applications in 
temp
 contain:


    File type	Count
    .pdf	22
    .jpg	5
    That's too small a sample. The "document type" column in each saved metadata file across a wider set of Idox applications would give you the real list. Until then, skip unsupported files and record them in limitations, rather than failing the run.

2. Check the quotes the model returns. The prompt already asks for document ID, page and an exact quote for every finding, which is good. Where the page has a text layer, a simple check that the quote appears in that page's text catches made-up evidence for almost no effort. Findings that fail the check can be marked down or flagged.

3. Every evidence entry has document_id: null and filename: null. Only page_number is filled in, and page numbers restart in each of the 3 documents, so "page 1" could be any of them. In practice the findings can't be traced to a source. Two likely causes:

- The page images carry no label, so the model can't link a document ID to an image.
- Nothing rejects null IDs.

    Fix both: add a short text label before each image in the request ("Attachment 12 = document 6007685, page 3"), and have parse_document_findings reject null document IDs. The quote check I suggested then becomes possible.

4. New, cheap check: compare the document coordinates with the polygon. The model found Easting 335926 / Northing 322794 on the application form, about 90 m from the LPA polygon. A simple distance check between the two sources flags a wrong site or a misread before spatial screening runs.

# Copilot brainstorm

Interim (pre-final) version of the document screening flow for the AI Triage PoC.
The final version adds per-document extraction, validation, merging and escalation.

## Steps

1. **Get the application record** *(built)*
   - Search the Idox site by reference.
   - Read summary, further information, document list and polygon.

2. **Rank the documents** *(new)*
   - One LLM call over the document list metadata (type, description, date, drawing number).
   - Output per document: `must` / `maybe` / `skip`, with a short reason.
   - `skip` only for a fixed list (public comments, neighbour letters, photos, other consultee responses).
   - Default to `must` when uncertain.

3. **Download documents asynchronously** *(new)*
   - Download `must` documents; optionally `maybe` documents.
   - Use one global concurrency limit from the implementation YAML, not a per-council limit.
   - Record failed or unavailable documents instead of stopping the run.

4. **Extract text** *(mostly built)*
   - Extract text per page for each downloaded PDF.
   - Record characters per page (flags pages with no text layer).
   - Record unsupported formats as limitations.

5. **Find and read the application form** *(prototype ready)*
   - Identify it by `Planning Portal Reference: PP-` in the first ~1,000 characters.
   - Extract fields with code: site address, postcode, easting/northing, proposal description, units, radio answers.
   - Each field returns value, page and status (`found` / `empty` / `not_on_form` / `invalid`).

6. **Extract from the remaining documents – two routes to compare**
   - **Route A – LLM (current approach):**
     - Send the selected documents to the LLM.
     - Ask for findings with document ID, page number and exact quote.
   - **Route B – Retrieval (RAG):**
     - Index page text with hybrid search (keyword + embeddings).
     - Query per required field; send only the retrieved pages to the LLM.
     - Same output format and references as Route A.

7. **Produce the output JSON**
   - **Top:** required fields (from the application form and the Idox record first, then the document findings).
   - **Bottom:** additional LLM context (assessments, permissions, other findings, limitations).
   - Each value keeps its source (document ID, page, quote).

8. **Hand over to spatial screening**
   - Spatial screening uses the polygon or coordinates from the required fields.
   - IRZ questions are answered using the required fields plus the additional context.

## Plan

- Create "implementations" folder to allow for different methods for document screening while retaining old code (done)
- Meta-data first. Currently the script downloads all docs then stores meta data, change this and ensure the document link is also captured in the meta data to allow for download after prioritisation (done)
- Add an "ignore_after" field. This will be a date field that will ignore all documents added to the lpa website after that date. Reason: these are all historical consultations and we want to mock live testing before the decision was made. This should be an optional in an "implementation" field that does nothing if not provided (done; same-day documents are included).
- Create new implementation. This implementations needs to retain the meta-data first approach and the ignore_after field (done):
    - get application from LPA website
    - rank documents using metadata and add prioritisation + justification to metadata. This requires a new prompt to be defiined.
   - download musts asynchronously, with maybe documents controlled by configuration (global concurrency set in the YAML file)
    - Extract text and rendered images as currently implemented, no additional features to add yet
    - Submit documents to an LLM as currently implemented, no need for RAG yet
    - Retrieve output json in a format consistent with what we have, no changes.
    - COnclude implementation


