"""Seeded attribute vocabulary: canonical keys, their cardinality, and synonyms that resolve to them.

These fix the original's "city vs location" drift on day one (D9): any listed synonym becomes an
exact AttrAlias match. New synonyms are learned at write time and stored the same way.
"""
from __future__ import annotations

# (canonical key, cardinality, description, synonyms)
ATTRIBUTE_GROUPS: list[tuple[str, str, str, list[str]]] = [
    ("residence", "one", "Where the entity currently lives",
     ["city", "location", "lives_in", "home", "home_city", "current_city", "address", "place_of_residence",
      "residence_city", "living_in", "based_in"]),
    ("employer", "one", "Organisation the entity works for",
     ["company", "works_at", "workplace", "work", "job_company", "organization", "organisation", "works_for"]),
    ("occupation", "one", "Job title or profession",
     ["job", "job_title", "title", "position", "profession", "role", "career"]),
    ("age", "one", "Age in years", ["years_old"]),
    ("name", "one", "Preferred full name", ["full_name", "first_name", "preferred_name"]),
    ("spouse", "one", "Current spouse or partner", ["partner", "husband", "wife", "married_to"]),
    ("relationship_status", "one", "Single, married, engaged, ...", ["marital_status"]),
    ("phone_number", "one", "Phone number", ["phone", "mobile", "cell", "telephone"]),
    ("email", "one", "Email address", ["email_address", "mail"]),
    ("favorite_color", "one", "Favourite colour", ["favourite_color", "favourite_colour", "favorite_colour"]),
    ("likes", "many", "Things the entity likes", ["like", "loves", "enjoys", "interests", "favorite_foods",
                                                  "preferences"]),
    ("dislikes", "many", "Things the entity dislikes", ["hates", "dislike"]),
    ("hobbies", "many", "Hobbies and pastimes", ["hobby", "pastimes"]),
    ("pets", "many", "Pets the entity has", ["pet", "animals"]),
    ("languages", "many", "Languages spoken", ["language", "speaks"]),
    ("skills", "many", "Skills", ["skill", "expertise"]),
    ("allergies", "many", "Allergies", ["allergy", "allergic_to"]),
    ("diet", "one", "Diet (vegetarian, vegan, ...)", ["dietary_preference", "diet_type"]),
    ("status", "one", "Current status of a project, task or thing", ["state", "project_status"]),
    ("owner", "one", "Who owns or leads it", ["lead", "owned_by", "led_by"]),
    ("deadline", "one", "Due date", ["due_date", "due"]),
]
