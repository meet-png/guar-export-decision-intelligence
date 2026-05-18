"""Model layer — the forecasting engine behind Pillar WHEN.

Turns the canonical price spine (:mod:`src.features.guar_price`) into a
forward price view and an honest, no-look-ahead measure of how much that
view can be trusted. The decision rule (SELL / WAIT / LOCK) and the
rupee-ROI sit on top of this in a later step; this package's only job is
to forecast price and to tell the truth about its own accuracy.
"""
