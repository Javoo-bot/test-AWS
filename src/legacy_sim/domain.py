"""
Reference data for the simulated legacy blood-screening LIS.

Scenario: donor screening laboratory (virology / blood screening). Assay panel
reflects the markers mandated for allogeneic blood donation screening in the EU
(Directive 2002/98/EC and national transpositions): NAT plus serology for
HIV-1/2, HBV and HCV, plus syphilis and anti-HBc.

LOINC codes are the public, canonical identifiers for the target system. The
legacy system predates that decision and uses proprietary local codes -- which
is precisely the mapping gap this migration has to resolve.
"""

# (legacy_code, legacy_name, loinc_code, loinc_name, result_kind, legacy_unit, target_unit)
# result_kind: 'QUAL' = qualitative (reactive / non-reactive), 'SCO' = signal-to-cutoff ratio
ASSAY_CATALOGUE = [
    ("VIR001", "HIV 1/2 NAT",        "69354-8", "HIV 1+2 IgG+IgM Ser Ql",        "QUAL", "",       ""),
    ("VIR002", "HCV NAT",            "11259-9", "HCV RNA NAA+probe Ql",          "QUAL", "",       ""),
    ("VIR003", "HBV NAT",            "29615-2", "HBV DNA NAA+probe Ql",          "QUAL", "",       ""),
    ("SER010", "HBsAg",              "5196-1",  "HBV surface Ag Ser Ql EIA",     "SCO",  "S/CO",   "ratio"),
    ("SER011", "Anti-HCV",           "13955-0", "HCV Ab Ser Ql EIA",             "SCO",  "S/CO",   "ratio"),
    ("SER012", "Anti-HIV 1/2",       "7918-6",  "HIV 1+2 Ab Ser Ql EIA",         "SCO",  "S/CO",   "ratio"),
    ("SER013", "Anti-HBc total",     "13952-7", "HBV core Ab Ser Ql EIA",        "SCO",  "S/CO",   "ratio"),
    ("SER014", "Syphilis TP",        "20507-0", "Reagin Ab Ser Ql RPR",          "SCO",  "S/CO",   "ratio"),
    ("IMM020", "ABO group",          "883-9",   "ABO group Bld Ql",              "QUAL", "",       ""),
    ("IMM021", "Rh(D) type",         "10331-7", "Rh Bld Ql",                     "QUAL", "",       ""),
    # Deliberate gap: local haemoglobin pre-donation check with no agreed target code.
    ("LOC900", "Hb pre-donation",    None,      None,                            "SCO",  "g/dL",   "g/L"),
    # Deliberate gap: retired in-house assay, still present in historical data.
    ("LOC901", "HTLV I/II legacy",   None,      None,                            "QUAL", "",       ""),
]

# Legacy result codes -> canonical target vocabulary.
# The legacy system accreted synonyms over 15 years; several mean the same thing.
RESULT_CODE_MAP = {
    "NR":       "NON_REACTIVE",
    "N":        "NON_REACTIVE",
    "NEG":      "NON_REACTIVE",
    "R":        "REACTIVE",
    "REAC":     "REACTIVE",
    "POS":      "REACTIVE",
    "IR":       "INITIAL_REACTIVE",
    "RR":       "REPEAT_REACTIVE",
    "INV":      "INVALID",
    "QNS":      "QUANTITY_NOT_SUFFICIENT",
    # Deliberate gap: undocumented sentinel found in historical rows only.
    "9999":     None,
    "":         None,
}

ABO_GROUPS = ["A", "B", "AB", "O"]
RH_TYPES = ["POS", "NEG"]

# Sites contributing donations, with their legacy timezone convention.
# The legacy LIS stored wall-clock local time with no offset; the target is UTC.
SITES = [
    ("BCN01", "Barcelona Central",  "Europe/Madrid"),
    ("BCN02", "Barcelona Nord",     "Europe/Madrid"),
    ("MAD01", "Madrid Norte",       "Europe/Madrid"),
    ("VLC01", "Valencia",           "Europe/Madrid"),
    ("LPA01", "Las Palmas",         "Atlantic/Canary"),  # different offset from the rest
]

# Names carrying characters that are lossy under Latin-1 -> UTF-8 mishandling.
FORENAMES = [
    "Núria", "Jordi", "Begoña", "Iñaki", "José", "María", "Ángel", "Sofía",
    "Martí", "Aitana", "Xavier", "Lucía", "Álvaro", "Mónica", "Joan", "Cristòfol",
]
SURNAMES = [
    "Puigdemont", "Ferrández", "Muñoz", "Peñalver", "Gómez", "Sáez", "Bofill",
    "Català", "Ibáñez", "Núñez", "Alcañiz", "Espinós", "Vílchez", "Rodríguez",
]
