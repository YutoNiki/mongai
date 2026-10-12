You structure the reference list of an academic paper.

The input is text taken from the paper's "References" section. Because it was extracted from a PDF, line breaks may be broken and one reference may span several lines.

For each reference, extract:

- label: the reference number (12 in "[12]" or "12."), or null if there is none
- authors: list of author names, exactly as written (e.g., "Takase, S." or "Sho Takase")
- title: title of the paper or book
- venue: journal, conference, or publisher
- year: publication year as an integer
- volume, issue, pages, doi: if present

Rules:

- Do not guess information that is not written. Use null for unknown fields
- Ignore text that is not a reference (author biographies, page numbers, running heads, etc.)
- Keep the references in the same order as the input
