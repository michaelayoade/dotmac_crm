"""Legacy imports for backwards compatibility.

This module re-exports fiber and splitter services from the main network module.
"""

from app.services.network_impl import (
    # Splitter services
    FdhCabinets,
    Splitters,
    SplitterPorts,
    fdh_cabinets,
    splitters,
    splitter_ports,
    # Fiber services
    FiberStrands,
    FiberSpliceClosures,
    FiberSplices,
    FiberSpliceTrays,
    FiberTerminationPoints,
    FiberSegments,
    fiber_strands,
    fiber_splice_closures,
    fiber_splices,
    fiber_splice_trays,
    fiber_termination_points,
    fiber_segments,
    # PON port splitter links
    PonPortSplitterLinks,
    pon_port_splitter_links,
)

__all__ = [
    # Splitter services
    "FdhCabinets",
    "fdh_cabinets",
    "Splitters",
    "splitters",
    "SplitterPorts",
    "splitter_ports",
    # Fiber services
    "FiberStrands",
    "fiber_strands",
    "FiberSpliceClosures",
    "fiber_splice_closures",
    "FiberSplices",
    "fiber_splices",
    "FiberSpliceTrays",
    "fiber_splice_trays",
    "FiberTerminationPoints",
    "fiber_termination_points",
    "FiberSegments",
    "fiber_segments",
    # PON port splitter links
    "PonPortSplitterLinks",
    "pon_port_splitter_links",
]
