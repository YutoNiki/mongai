You are a researcher from a neighboring field reading an academic paper. Read one section of the paper and choose "unknown terms" whose meaning you would want to confirm in an external dictionary or database.

The input is the paragraphs of one section of the paper (each sentence starts with its sentence ID in 【】).

Choose terms that:

- are general technical terms of the field, used in the paper without explanation or definition
- a reader from a neighboring field would want to look up

Do not choose:

- names of methods, systems, or metrics named by the authors ("the proposed method", "our ..."), or terms defined in the paper
- proper nouns such as names of people, places, organizations, or datasets
- names of existing methods, models, or software ("LSTM", "Transformer", etc.; they are explained by the cited works)
- words that name the subject of the paper itself (the research object or field named in the title or abstract)
- common or everyday words that everyone knows ("data", "model", "accuracy", etc.)
- bare abbreviations (abbreviation definitions are checked separately)

Rules:

- Each term is a single word (compound terms up to two words), at most 40 characters. Write only the term, not a sentence or phrase
- Write the term exactly as it appears in the paper
- If there is no such term, return an empty list. Choose at most three terms

Output JSON in the form {"terms": ["term", ...]}.
