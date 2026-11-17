
1. First pass should check the required fields and use them to answer the checklist items in the format example below

    ```
    {
    "guidance_id": "1h",
    "answer": boolean,
    "reason": string,
    "evidence": [
        {
        "stage": "case_intake" | "document_screening" | "spatial_screening" ,
        "source": string,
        "quote": "..."
        }
    ],
    "confidence": "high"|"medium"|"low"
    }
    ```
    The evidence, reason and confidence fields should be taken from the field/s that informed the decision.

    Then the overall classification of necessary/unnecessary will be a check of all the answers. If all are false, then unnecessary, if at least one is true, then necessary.

    No dedicated reasoning, confidence or evidence will be implmeneted here for the overall classification.

2. The second pass will add the confidence Overall confidence will be calculated from the confidences of the overall items in the checklist (I am wondering how though, do we assign a value eg low=0, medium=50 and high=100, or take the average category and keep it to low, medium high, probably the latter). Or it can be:
    - Necessary: Highest confidence among the yes answers
    - Unnecessary: Lowest confidence among all no answers

3. The third pass will add evidence and reasoning using an llm