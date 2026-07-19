
from google.genai import types

SYSTEM_PROMPT = """You are the Saathi Verification Agent. Your task is to inspect
a defect photo and decide whether the delivered goods match the order
specification, using live tools to gather context before you decide.

You have FOUR tools:
1. get_order_spec        — call this first to see what the order actually required.
2. get_workshop_history   — call this second to see if this kind of defect is a
                             pattern for the workshop that produced this sub-lot.
3. get_reference_image    — optional: fetches a reference photo of a correctly made
                             item of this product_type, so you can compare it directly
                             against the defect photo instead of relying on the text
                             spec alone. Call it when the suspected defect is visual
                             (colour, shape, print, size). Not every product_type has
                             one on file — if none is available, reason from the text
                             spec alone; do not treat a missing reference as a defect
                             signal either way.
4. submit_verdict         — call this LAST, exactly once, after the tools above.

Do not call submit_verdict before you have called both get_order_spec and
get_workshop_history at least once.

Verdict rules:
- OK            : The item matches the specification. No defect, or defect is cosmetic
                  and not described in the spec.
- DEFECT        : A clear, spec-relevant defect is visible (wrong colour, size, material,
                  structural damage, incorrect print). Set fault_party = "workshop".
- SPEC_AMBIGUITY: The specification is too vague to make a determination. You cannot tell
                  whether this is defective without a clearer spec.
                  Set fault_party = "buyer".

Confidence guidelines:
- ≥ 0.90 : You are certain.
- 0.70–0.89 : Clear lean but some ambiguity.
- < 0.70 : Too uncertain — prefer SPEC_AMBIGUITY over a low-confidence DEFECT.

If get_workshop_history shows a high recent_defect_rate or common_failure_modes
matching what you see in the photo, that raises your confidence in a DEFECT
verdict — a repeat pattern is stronger evidence than an isolated incident.

Explanation must be ≤ 3 sentences, written in English regardless of any
other language present in the order, description, or photo. (A separate,
non-agentic translation step handles other languages for display — your
explanation is the single English source it translates from.) Write for a
non-technical audience.
Do NOT mention workshop names, internal IDs, or system details.
Do NOT speculate about cause beyond what the tools and photo show."""


GET_ORDER_SPEC_DECLARATION = types.FunctionDeclaration(
    name="get_order_spec",
    description="Fetch what this order actually required, to compare against the photo.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "order_id": types.Schema(
                type=types.Type.INTEGER, description="The order ID to look up."
            ),
        },
        required=["order_id"],
    ),
)

GET_WORKSHOP_HISTORY_DECLARATION = types.FunctionDeclaration(
    name="get_workshop_history",
    description=(
        "Fetch this workshop's trust score, recent defect rate, and recent "
        "defect explanations, to check whether this looks like a pattern."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "workshop_id": types.Schema(
                type=types.Type.INTEGER, description="The workshop ID to look up."
            ),
        },
        required=["workshop_id"],
    ),
)

GET_REFERENCE_IMAGE_DECLARATION = types.FunctionDeclaration(
    name="get_reference_image",
    description=(
        "Fetch a reference photo of a correctly made item of this order's "
        "product_type, to compare visually against the defect photo. Not "
        "every product_type has one on file — check the tool result's "
        "'found' field."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "order_id": types.Schema(
                type=types.Type.INTEGER,
                description="The order ID whose product_type's reference photo to fetch.",
            ),
        },
        required=["order_id"],
    ),
)

SUBMIT_VERDICT_DECLARATION = types.FunctionDeclaration(
    name="submit_verdict",
    description="Submit your final verification verdict. Call this exactly once, last.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "verdict": types.Schema(
                type=types.Type.STRING,
                enum=["OK", "DEFECT", "SPEC_AMBIGUITY"],
                description="Your verdict on the delivered item.",
            ),
            "fault_party": types.Schema(
                type=types.Type.STRING,
                enum=["workshop", "buyer", "none"],
                description="Who bears responsibility. 'none' for OK verdicts.",
            ),
            "confidence": types.Schema(
                type=types.Type.NUMBER,
                minimum=0.0,
                maximum=1.0,
                description="Your confidence in this verdict.",
            ),
            "explanation": types.Schema(
                type=types.Type.STRING,
                description="1–3 sentences for the buyer and workshop owner.",
            ),
        },
        required=["verdict", "fault_party", "confidence", "explanation"],
    ),
)
TOOLS = [
    types.Tool(
        function_declarations=[
            GET_ORDER_SPEC_DECLARATION,
            GET_WORKSHOP_HISTORY_DECLARATION,
            GET_REFERENCE_IMAGE_DECLARATION,
            SUBMIT_VERDICT_DECLARATION,
        ]
    )
]
