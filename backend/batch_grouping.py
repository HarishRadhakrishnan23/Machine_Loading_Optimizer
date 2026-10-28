"""
batch_grouping.py — the batch/fixture key for Engine 1 (Models E/F). The only function here is the live
`compute_batch_key` (imported by dispatch_batching / dispatch_orders); the Model C grouping helpers that
once lived here were removed in Phase 3.

Batch/fixture-key logic (Model E).

A BATCH groups Production Orders with the same SIZE_INCH~CLASS~MOC~DESIGN.
This is also the fixture/locator lookup key into MCH_ITEMWISE_FIXTURE_LOCATOR
(CLAUDE.md "Batch / fixture key — used everywhere, all tables (Model E)").

Field order is fixed: SIZE_INCH ~ CLASS ~ MOC ~ DESIGN. This must always be
built from the raw typed WIP columns directly — never parsed out of
ITEM_CATEGORY, whose own concatenation order is Size~Class~Design~MOC (a
different order) and which silently drops the DESIGN segment when it is blank,
shifting MOC into DESIGN's position.

Safety stock orders (CDD = NULL) are included in batches but flagged separately
so operators can manually exclude them during execution if needed.
"""


def compute_batch_key(size_inch: str, class_val: str, moc: str, design: str) -> str:
    """
    Compute the Model E batch/fixture key: SIZE_INCH~CLASS~MOC~DESIGN.

    Example:
        size_inch = "3", class_val = "300", moc = "CS", design = "DFS"
        returns "3~300~CS~DFS"

    Args:
        size_inch: SIZE_INCH (e.g., "3", "6", "10")
        class_val: CLASS (e.g., "150", "300")
        moc: MOC (e.g., "CS", "SS")
        design: DESIGN (e.g., "DFS", "LUG")

    Returns:
        Batch key string: "SIZE_INCH~CLASS~MOC~DESIGN"
    """
    return f"{size_inch}~{class_val}~{moc}~{design}"
