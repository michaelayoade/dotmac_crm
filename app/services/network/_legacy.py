"""Legacy imports for backwards compatibility.

This module re-exports fiber and splitter services from the main network module.
"""

from app.services.network_impl import (
    # Splitter services
    FdhCabinets,
    FiberSegments,
    FiberSpliceClosures,
    FiberSplices,
    FiberSpliceTrays,
    # Fiber services
    FiberStrands,
    FiberTerminationPoints,
    # PON port splitter links
    PonPortSplitterLinks,
    SplitterPorts,
    Splitters,
    fdh_cabinets,
    fiber_segments,
    fiber_splice_closures,
    fiber_splice_trays,
    fiber_splices,
    fiber_strands,
    fiber_termination_points,
    pon_port_splitter_links,
    splitter_ports,
    splitters,
)

__all__ = [
    # Splitter services
    "FdhCabinets",
    "FiberSegments",
    "FiberSpliceClosures",
    "FiberSpliceTrays",
    "FiberSplices",
    # Fiber services
    "FiberStrands",
    "FiberTerminationPoints",
    # PON port splitter links
    "PonPortSplitterLinks",
    "SplitterPorts",
    "Splitters",
    "fdh_cabinets",
    "fiber_segments",
    "fiber_splice_closures",
    "fiber_splice_trays",
    "fiber_splices",
    "fiber_strands",
    "fiber_termination_points",
    "pon_port_splitter_links",
    "splitter_ports",
    "splitters",
]
