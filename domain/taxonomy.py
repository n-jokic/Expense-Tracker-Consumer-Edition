"""
domain/taxonomy.py — canonical category taxonomy (Streamlit-free).

Single source of truth for CATEGORIES / CAT_LIST / ALL_SUBCATS /
TAXONOMY_MIGRATION / _TAXONOMY_LOOKUP / CATEGORY_RENAMES and the remap helpers.
R6 will reduce utils.py to a thin shim re-exporting from here.
"""

from __future__ import annotations

CATEGORIES: dict[str, list[str]] = {
    "Housing & Utilities": ["Rent / Mortgage","Electricity","Gas & Heating","Water",
                            "Internet & Phone","Home Insurance","Building Maintenance","Furniture & Appliances"],
    "Groceries":           ["Groceries"],
    "Dining Out":          ["Restaurants & Takeaway","Coffee & Snacks","Food Delivery","Work Lunch"],
    "Transport":           ["Fuel","Public Transit","Taxi / Uber","Car Insurance",
                            "Car Maintenance","Parking","Tolls"],
    "Travel":              ["Flights & Trains","Hotels & Lodging","Tours & Activities"],
    "Health":              ["Gym & Fitness","Pharmacy","Doctor / Specialist","Dental",
                            "Supplements","Mental Health"],
    "Entertainment":       ["Streaming Services","Cinema & Theater","Concerts & Events",
                            "Going Out","Hobbies","Books & Courses"],
    "Shopping":            ["Clothing & Accessories","Beauty & Skincare","Haircut & Grooming","Gifts"],
    "Subscriptions & Software": ["Subscriptions & Software"],
    "Fees & Taxes":        ["Taxes & Fees","Bank & ATM Fees"],
    "Loans & Debt":        ["Loan Repayment","Interest","Credit Card","Other Debt"],
    "Other":               ["Charity & Donations","Miscellaneous"],
}

INCOME_SOURCES: list[str] = ["Primary Salary","Freelance / Side Income","Investment Returns","Rental Income","Other"]
INCOME_TYPES: list[str]   = ["Salary","Hourly","Bonus / Raise","Freelance","Investment","Rental","Other"]
SAVINGS_GOALS: list[str]  = ["Emergency Fund","Vacation / Travel","Investment Account","Down Payment","Other"]

CAT_LIST: list[str]    = list(CATEGORIES.keys())
ALL_SUBCATS: list[str] = sorted({s for subs in CATEGORIES.values() for s in subs})

TAXONOMY_MIGRATION: list[tuple[str, str, str, str]] = [
    ("Housing", "Rent / Mortgage",        "Housing & Utilities", "Rent / Mortgage"),
    ("Housing", "Electricity",            "Housing & Utilities", "Electricity"),
    ("Housing", "Gas & Heating",          "Housing & Utilities", "Gas & Heating"),
    ("Housing", "Water",                  "Housing & Utilities", "Water"),
    ("Housing", "Internet & Phone",       "Housing & Utilities", "Internet & Phone"),
    ("Housing", "Home Insurance",         "Housing & Utilities", "Home Insurance"),
    ("Housing", "Building Maintenance",   "Housing & Utilities", "Building Maintenance"),
    ("Housing", "Furniture & Appliances", "Housing & Utilities", "Furniture & Appliances"),
    ("Housing", "",                       "Housing & Utilities", ""),
    ("Food & Dining", "Groceries",               "Groceries", "Groceries"),
    ("Food & Dining", "Restaurants & Takeaway",  "Dining Out", "Restaurants & Takeaway"),
    ("Food & Dining", "Coffee & Snacks",         "Dining Out", "Coffee & Snacks"),
    ("Food & Dining", "Food Delivery",           "Dining Out", "Food Delivery"),
    ("Food & Dining", "Work Lunch",              "Dining Out", "Work Lunch"),
    ("Food & Dining", "",                        "Groceries", "Groceries"),
    ("Transport", "Fuel",           "Transport", "Fuel"),
    ("Transport", "Public Transit", "Transport", "Public Transit"),
    ("Transport", "Taxi / Uber",    "Transport", "Taxi / Uber"),
    ("Transport", "Car Insurance",  "Transport", "Car Insurance"),
    ("Transport", "Car Maintenance","Transport", "Car Maintenance"),
    ("Transport", "Parking",        "Transport", "Parking"),
    ("Transport", "Tolls",          "Transport", "Tolls"),
    ("Transport", "Flights & Trains", "Travel", "Flights & Trains"),
    ("Transport", "",               "Transport", ""),
    ("Health", "Gym & Fitness",       "Health", "Gym & Fitness"),
    ("Health", "Pharmacy",            "Health", "Pharmacy"),
    ("Health", "Doctor / Specialist", "Health", "Doctor / Specialist"),
    ("Health", "Dental",              "Health", "Dental"),
    ("Health", "Supplements",         "Health", "Supplements"),
    ("Health", "Mental Health",       "Health", "Mental Health"),
    ("Health", "",                    "Health", ""),
    ("Entertainment", "Streaming Services",  "Entertainment", "Streaming Services"),
    ("Entertainment", "Cinema & Theater",    "Entertainment", "Cinema & Theater"),
    ("Entertainment", "Concerts & Events",   "Entertainment", "Concerts & Events"),
    ("Entertainment", "Going Out",           "Entertainment", "Going Out"),
    ("Entertainment", "Hobbies",             "Entertainment", "Hobbies"),
    ("Entertainment", "Books & Courses",     "Entertainment", "Books & Courses"),
    ("Entertainment", "Vacation / Travel",   "Travel", "Tours & Activities"),
    ("Entertainment", "Hotels & Lodging",    "Travel", "Hotels & Lodging"),
    ("Entertainment", "",                    "Entertainment", ""),
    ("Personal", "Clothing & Accessories", "Shopping", "Clothing & Accessories"),
    ("Personal", "Beauty & Skincare",      "Shopping", "Beauty & Skincare"),
    ("Personal", "Haircut & Grooming",     "Shopping", "Haircut & Grooming"),
    ("Personal", "Gifts",                  "Shopping", "Gifts"),
    ("Personal", "",                       "Shopping", ""),
    ("Loans & Debt", "Loan Repayment", "Loans & Debt", "Loan Repayment"),
    ("Loans & Debt", "Interest",       "Loans & Debt", "Interest"),
    ("Loans & Debt", "Credit Card",    "Loans & Debt", "Credit Card"),
    ("Loans & Debt", "Other Debt",     "Loans & Debt", "Other Debt"),
    ("Other", "Subscriptions & Software", "Subscriptions & Software", "Subscriptions & Software"),
    ("Other", "Taxes & Fees",             "Fees & Taxes", "Taxes & Fees"),
    ("Other", "Charity & Donations",      "Other", "Charity & Donations"),
    ("Other", "Miscellaneous",            "Other", "Miscellaneous"),
    ("Other", "",                         "Other", "Miscellaneous"),
]

_TAXONOMY_LOOKUP: dict[tuple[str, str], tuple[str, str]] = {
    (oc, os): (nc, ns) for oc, os, nc, ns in TAXONOMY_MIGRATION
}

CATEGORY_RENAMES: dict[str, str] = {
    "Housing": "Housing & Utilities",
    "Food & Dining": "Groceries",
    "Personal": "Shopping",
}

_TRAVEL_SUBCATS: set[str] = {"Vacation / Travel", "Hotels & Lodging", "Flights & Trains"}


def remap_category_subcategory(category, subcategory=""):
    cat = category or ""
    sub = subcategory or ""
    return _TAXONOMY_LOOKUP.get((cat, sub), (cat, sub))


def remap_fun_categories(entries):
    out: list[str] = []
    for e in (entries or []):
        e = (e or "").strip()
        if e == "Food & Dining":
            out.append("Dining Out")
        elif e == "Groceries":
            continue
        elif e:
            out.append(e)
    return list(dict.fromkeys(out))


def remap_travel_categories(entries):
    out: list[str] = []
    for e in (entries or []):
        e = (e or "").strip()
        if not e:
            continue
        if " \u203a " in e:
            _cat, sub = e.split(" \u203a ", 1)
            if sub.strip() in _TRAVEL_SUBCATS:
                out.append("Travel")
                continue
        elif e == "Entertainment":
            out.append("Travel")
            continue
        out.append(e)
    return list(dict.fromkeys(out))
