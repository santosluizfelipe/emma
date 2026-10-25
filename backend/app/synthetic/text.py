"""Sentence banks for synthetic visit notes.

Routine sentences carry no risk. Event sentences carry exactly one signal each
(category + severity), so the ground truth is known by construction. Trap sentences
mention risk words in a negated or reassuring way and must produce no signal.
Placeholders: {first} first name, {pron} he/she, {obj} him/her, {pos} his/her.
"""

from __future__ import annotations

ROUTINE_OPENERS = [
    "I attended {first} for the {slot} visit and carried out the planned routine.",
    "{slot_title} visit completed for {first} as planned.",
    "I supported {first} with the planned {slot} call.",
]
ROUTINE_BODY = [
    "Care was delivered in line with the care plan.",
    "{first} was comfortable throughout.",
    "All planned tasks were completed with dignity and privacy maintained.",
    "{first} was settled when I left.",
]
ROUTINE_CLOSERS = [
    "No concerns were noted.",
    "There were no concerns to report.",
    "Care was delivered as normal with nothing of concern.",
]

ACTIVITIES = {
    "morning": ["Personal Care", "Medication", "Breakfast", "Continence Care"],
    "lunch": ["Lunch", "Medication", "Welfare Check"],
    "evening": ["Evening Meal", "Medication", "Continence Care"],
}
ROUTINE_COMMENTS = {
    "Personal Care": "Assisted with personal care as usual.",
    "Medication": "Medication was taken as prescribed.",
    "Breakfast": "Breakfast was prepared and eaten.",
    "Continence Care": "Continence care completed.",
    "Lunch": "Lunch was provided and eaten.",
    "Welfare Check": "Welfare check completed; settled.",
    "Evening Meal": "Evening meal was provided and supported.",
}
SLOT_TIMES = {"morning": ("08:00:00", "08:45:00"), "lunch": ("12:30:00", "13:15:00"),
              "evening": ("17:30:00", "18:15:00")}

# (category, severity) -> sentence variants. Escalated variants say who was informed.
EVENTS = {
    ("falls_mobility", "high"): [
        "While assisting {obj} to stand from the chair, {pron} had a slight near-fall but was steadied straight away; the office was informed.",
        "{first} had a fall in the bathroom before the visit and was found on the floor; the GP was called.",
    ],
    ("falls_mobility", "medium"): [
        "{first} was a little more unsteady on {pos} feet today and used the frame throughout.",
    ],
    ("cognition", "low"): [
        "{first} was slightly unsure about the day/date at first, so I gently reoriented {obj}.",
    ],
    ("cognition", "medium"): [
        "On arrival {first} appeared confused about {pos} surroundings and needed reassurance.",
    ],
    ("intake", "low"): [
        "{first} had a reduced appetite and ate about half of the meal.",
    ],
    ("intake", "medium"): [
        "{first} declined fluids at first and only took a little after encouragement.",
        "Poor intake again noted at breakfast.",
    ],
    ("respiratory", "low"): [
        "{first} was mildly breathless on exertion during personal care.",
    ],
    ("respiratory", "medium"): [
        "A productive cough was noted today.",
        "{first} has been using {pos} inhaler more often than usual.",
    ],
    ("respiratory", "high"): [
        "{first} was breathless at rest this morning.",
        "{first} had a cough that seemed worse than usual and the sputum was discoloured; this was flagged to the office.",
    ],
    ("mood", "medium"): [
        "{first} was very withdrawn today and gave minimal engagement throughout the visit.",
    ],
    ("skin_injury", "high"): [
        "Bruising noted to {pos} left forearm; reported to office.",
    ],
    ("safeguarding", "high"): [
        "{first} expressed worry that money was going missing from the property; informed office.",
    ],
}
# Goes in the Medication task comment while the task stays "Completed"
MEDICATION_NOT_GIVEN = "Family reported not to have given morning medication again; escalated to office."

TRAPS = [
    "{first} was comfortable and in no distress throughout the visit.",
    "There were no signs of bruising during personal care.",
    "{first} was not confused today and chatted happily.",
    "{first} mobilised with the frame and no injury was noted.",
]

CONDITIONS = {
    "falls_mobility": ("frailty and reduced mobility", "falls, unsteadiness and reduced mobility"),
    "cognition": ("dementia", "disorientation, confusion and wandering"),
    "intake": ("frailty", "poor intake of food and fluids"),
    "respiratory": ("COPD", "breathlessness and chest infection"),
    "mood": ("low mood", "low mood and withdrawal"),
    "skin_injury": ("fragile skin", "skin damage and pressure areas"),
    "medication": ("type 2 diabetes", "missed medication"),
    "safeguarding": ("anxiety", "financial vulnerability"),
}

FIRST_NAMES = [
    ("Margaret", "F"), ("Harold", "M"), ("Jean", "F"), ("Patricia", "F"), ("Stanley", "M"),
    ("Alfred", "M"), ("Doris", "F"), ("Raymond", "M"), ("Iris", "F"), ("Cynthia", "F"),
    ("Arthur", "M"), ("Edith", "F"), ("Walter", "M"), ("Mabel", "F"), ("Leonard", "M"),
    ("Irene", "F"), ("Frank", "M"), ("Vera", "F"), ("Ernest", "M"), ("Gladys", "F"),
    ("Albert", "M"), ("Hilda", "F"), ("Cyril", "M"), ("Ruby", "F"), ("Norman", "M"),
]
LAST_NAMES = [
    "Hughes", "Timms", "Whitmore", "Lowe", "Grant", "Nkemelu", "Whitfield", "Clarke", "Bennett",
    "Ofori", "Patel", "Okafor", "Davies", "Murphy", "Kowalski", "Singh", "Evans", "Adeyemi",
    "Brown", "Novak", "Ahmed", "Taylor", "Mensah", "Walsh", "Khan", "Roberts", "Ward", "Morris",
]
CARER_NAMES = [
    "Amara Okafor", "Ben Fletcher", "Chloe Ramsay", "Dev Sharma", "Elena Novak", "Femi Adeyemi",
    "Grace Bello", "Harry Coleman", "Isla Munro", "Jakub Nowak", "Kemi Ade", "Liam Byrne",
]
