"""
batch_grouping.py — Batch-aware scheduling logic for Engine 1.

A BATCH groups Production Orders with the same SIZE~CLASS~DESIGN (excluding MOC).
All pieces in a batch are scheduled on the SAME MACHINE for each operation,
to minimize setup costs and maintain batch continuity.

Safety stock orders (CDD = NULL) are included in batches but flagged separately
so operators can manually exclude them during execution if needed.
"""

from typing import Dict, Set, Tuple
from datetime import datetime
import pandas as pd


def compute_batch_key(size_inch: str, class_val: str, design: str) -> str:
    """
    Compute batch key from SIZE~CLASS~DESIGN (excluding MOC).

    Example:
        size_inch = "3", class_val = "300", design = "DFS"
        returns "3~300~DFS"

    Args:
        size_inch: SIZE_INCH (e.g., "3", "6", "10")
        class_val: CLASS (e.g., "150", "300")
        design: DESIGN (e.g., "DFS", "LUG")

    Returns:
        Batch key string: "SIZE~CLASS~DESIGN"
    """
    return f"{size_inch}~{class_val}~{design}"


def group_orders_by_batch(wip_df: pd.DataFrame) -> Dict[str, list]:
    """
    Group production orders by batch key (SIZE~CLASS~DESIGN).

    Returns a dict: batch_key → list of (PRODUCTION_ORDER, OPERATION) tuples

    Args:
        wip_df: DataFrame with columns: PRODUCTION_ORDER, OPERATION,
                SIZE_INCH, CLASS, DESIGN, CDD

    Returns:
        Dict mapping batch_key (e.g., "3~300~DFS") to list of task keys
        Example: {
            "3~300~DFS": [("XX0000001", 10), ("XX0000002", 10), ("XX0000003", 10)],
            "6~150~LUG": [("XX0000004", 10), ("XX0000005", 10)]
        }
    """
    batch_groups = {}

    for _, row in wip_df.iterrows():
        # Compute batch key
        batch_key = compute_batch_key(
            str(row["SIZE_INCH"]),
            str(row["CLASS"]),
            str(row["DESIGN"])
        )

        # Task key (OPERATION is the operation sequence number)
        task_key = (str(row["PRODUCTION_ORDER"]), float(row["OPERATION"]))

        # Group by batch key
        if batch_key not in batch_groups:
            batch_groups[batch_key] = []
        batch_groups[batch_key].append(task_key)

    return batch_groups


def is_safety_stock(cdd) -> bool:
    """
    Check if an order is safety stock (CDD = NULL).

    Args:
        cdd: Committed Delivery Date (from MCH_WIP.CDD)

    Returns:
        True if CDD is None/NULL, False otherwise
    """
    return cdd is None or pd.isna(cdd)


def build_batch_task_map(wip_df: pd.DataFrame) -> Dict[Tuple, str]:
    """
    Build a map: (PRODUCTION_ORDER, OPERATION) → BATCH_KEY

    This allows Engine 1 to quickly look up which batch a task belongs to.

    Args:
        wip_df: DataFrame with WIP data

    Returns:
        Dict mapping task_key → batch_key
        Example: {
            ("QS1000575", 10): "10~150~DFS",
            ("QS1000575", 20): "10~150~DFS",
            ("VN1003405", 50): "8~300~CS"
        }
    """
    task_to_batch = {}

    for _, row in wip_df.iterrows():
        task_key = (str(row["PRODUCTION_ORDER"]), float(row["OPERATION"]))
        batch_key = compute_batch_key(
            str(row["SIZE_INCH"]),
            str(row["CLASS"]),
            str(row["DESIGN"])
        )
        task_to_batch[task_key] = batch_key

    return task_to_batch


def build_safety_stock_map(wip_df: pd.DataFrame) -> Dict[str, bool]:
    """
    Build a map: PRODUCTION_ORDER → is_safety_stock

    This allows the output layer to flag safety stock orders in MCH_SCHEDULE_OUTPUT.

    Args:
        wip_df: DataFrame with WIP data

    Returns:
        Dict mapping PRODUCTION_ORDER → True if safety stock, False otherwise
        Example: {
            "QS1000575": False,
            "XX0000002": True,  # CDD = NULL
            "VN1003405": False
        }
    """
    safety_stock_map = {}

    for order in wip_df["PRODUCTION_ORDER"].unique():
        # Check if this order has CDD = NULL
        order_data = wip_df[wip_df["PRODUCTION_ORDER"] == order]
        # All rows of an order should have the same CDD
        cdd = order_data.iloc[0]["CDD"]
        safety_stock_map[str(order)] = is_safety_stock(cdd)

    return safety_stock_map


# Example usage for testing
if __name__ == "__main__":
    # Test batch key computation
    print("[Test] Batch key computation:")
    batch_key = compute_batch_key("3", "300", "DFS")
    print(f"  compute_batch_key('3', '300', 'DFS') = '{batch_key}'")
    assert batch_key == "3~300~DFS", "Batch key format incorrect"
    print("  [OK] PASSED")

    # Test safety stock detection
    print("\n[Test] Safety stock detection:")
    print(f"  is_safety_stock(None) = {is_safety_stock(None)}")
    print(f"  is_safety_stock('2026-08-27') = {is_safety_stock('2026-08-27')}")
    assert is_safety_stock(None) == True
    assert is_safety_stock("2026-08-27") == False
    print("  [OK] PASSED")

    print("\n[OK] All batch grouping tests passed!")
