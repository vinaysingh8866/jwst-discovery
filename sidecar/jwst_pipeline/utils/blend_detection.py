"""Blend group identification via KD-tree proximity search."""
import logging
from collections import deque

import numpy as np
from scipy.spatial import cKDTree

logger = logging.getLogger(__name__)


def identify_blend_groups(
    x: np.ndarray,
    y: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    proximity_factor: float = 3.0,
    min_proximity_pix: float = 10.0,
    max_group_size: int = 15,
) -> list[list[int]]:
    """Identify groups of blended sources based on proximity.

    Two sources are considered potentially blended if their separation is
    less than ``max(proximity_factor * r_half, min_proximity_pix)`` where
    ``r_half = sqrt(a * b)`` is the geometric mean half-light radius.

    Connected components are found via BFS and capped at *max_group_size*
    to prevent runaway in dense cluster cores.

    Parameters
    ----------
    x, y : ndarray
        Source pixel coordinates.
    a, b : ndarray
        Semi-major and semi-minor axes from SEP (pixels).
    proximity_factor : float
        Blend threshold as multiple of r_half.
    min_proximity_pix : float
        Absolute minimum proximity threshold (pixels).
    max_group_size : int
        Maximum sources per blend group.

    Returns
    -------
    list of list of int
        Each inner list contains source indices that form a blend group
        (always >= 2 members).
    """
    n = len(x)
    if n < 2:
        return []

    # Compute half-light radii
    r_half = np.sqrt(np.maximum(a, 0.5) * np.maximum(b, 0.5))

    # Build KD-tree
    coords = np.column_stack([x, y])
    tree = cKDTree(coords)

    # Find all pairs within the maximum possible search radius
    max_radius = max(float(np.max(r_half) * proximity_factor), min_proximity_pix)
    pairs = tree.query_pairs(max_radius, output_type="ndarray")

    if len(pairs) == 0:
        return []

    # Filter pairs by per-source proximity threshold
    i_arr, j_arr = pairs[:, 0], pairs[:, 1]
    sep_dist = np.sqrt((x[i_arr] - x[j_arr]) ** 2 + (y[i_arr] - y[j_arr]) ** 2)
    # Use the larger r_half of the two sources in each pair
    r_max = np.maximum(r_half[i_arr], r_half[j_arr])
    threshold = np.maximum(proximity_factor * r_max, min_proximity_pix)
    close_mask = sep_dist < threshold
    close_pairs = pairs[close_mask]

    if len(close_pairs) == 0:
        return []

    # Build adjacency list
    adj: dict[int, set[int]] = {}
    for i, j in close_pairs:
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)

    # BFS connected components
    visited = set()
    groups: list[list[int]] = []

    for start in adj:
        if start in visited:
            continue
        component: list[int] = []
        queue = deque([start])
        visited.add(start)
        while queue and len(component) < max_group_size:
            node = queue.popleft()
            component.append(node)
            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        if len(component) >= 2:
            groups.append(sorted(component))

    logger.info(
        "Found %d blend groups containing %d sources (of %d total)",
        len(groups), sum(len(g) for g in groups), n,
    )
    return groups
