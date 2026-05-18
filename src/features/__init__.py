"""Product feature layer — derived series the v2 pillars consume.

Everything here turns the canonical ``exports_clean`` dataset into the
specific decision inputs the *Guar Export Decision Intelligence* product
needs. The first and most load-bearing of these is the canonical guar
price series (:mod:`src.features.guar_price`) — both product pillars
(WHEN to sell, WHERE to sell) forecast and reason off this one series, so
it is built once, made robust, tested, and frozen as the spine.
"""
