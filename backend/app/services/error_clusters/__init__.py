"""Fehler-Cluster: Muster in Fehlermeldungen finden, Ausbrüche erkennen, melden.

Trichter wie in hostgroup_analysis: Mustererkennung (miner) und Regeln (rules) laufen
ohne LLM; nur bestätigte Cluster gehen an die KI-Einschätzung. Ob gemeldet wird,
entscheiden die Regeln — das LLM schreibt nur den Text dazu.

Gezählt wird JEDE Zeile: anders als der Feed-Collector (alert_aggregator), der pro
dedup_key eine Meldung behält, ergeben hier 10 000 gleiche Zeilen den Zählwert 10 000.
"""
