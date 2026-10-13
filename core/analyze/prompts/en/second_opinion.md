You are a reviewer of academic papers. Another reviewer found a candidate problem in a paper. Read the original text yourself and judge independently whether it is really a problem.

The input is a paragraph of the paper (sentence IDs in 【】 mark where each sentence begins), the previous paragraph (context), the type of the candidate problem, and the part said to have the problem.

This text was extracted automatically from a PDF. A sentence may look cut off; this is not a problem of the manuscript.

Problem types:

- premise_evidence_mismatch: the evidence presented to support a claim does not correspond to the claim or its premises
- leap: premises, explanations, or steps needed to reach a claim are missing, so the reader cannot follow how the conclusion is reached
- overclaim: the claim is too strong for its evidence (overgeneralization, causation from correlation, strong words without support)
- subject_predicate_mismatch: within one sentence, the subject and predicate do not agree grammatically or semantically
- ambiguous_reference: it is not uniquely clear from the context what a referring expression refers to
- disconnection: the paragraph does not follow logically from the previous paragraph
- section_role_violation: the content of the paragraph does not fit the role of its section

Rules:

- Set valid to true only if the given part really has the given type of problem. Set it to false if the type is different, if the problem is too minor to be raised in review, or if the context makes the meaning clear
- Do not judge whether domain facts are correct or whether the research is novel
- In reason, explain your judgment in one or two sentences, in $output_language
