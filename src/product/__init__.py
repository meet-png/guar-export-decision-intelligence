"""Product synthesis layer — turns the pillars into the exporter's rupees.

`features` (price spine, market radar) and `model` (WHEN hedge signal)
speak in $/kg and %. A businessman does not buy $/kg; he buys a rupee
number on *his own* tonnage. This package is the connective tissue:
given a (simulated, per the data decision) exporter profile, it converts
WHEN's downside and WHERE's price differentials into ₹/year — the line
that decides whether the product is worth paying for.
"""
