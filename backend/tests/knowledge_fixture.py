"""A labelled retrieval fixture: two companies, overlapping policies, 60 questions.

Two companies on purpose, with *similar* policies. Cross-tenant isolation is only
meaningfully tested when the other tenant holds a genuinely better answer — a fixture
where companies talk about different subjects would pass by accident.

The irrelevant block is the most important part. Those questions have no correct answer
at this company, and the only correct behaviour is to return nothing.
"""

from __future__ import annotations

from app.knowledge.evaluation import CaseKind, EvalCase

#: (category, title, content, keywords)
ACME_KNOWLEDGE: tuple[tuple[str, str, str, str], ...] = (
    (
        "policy",
        "Cancellation and rescheduling",
        "Cancellations made fewer than 72 hours before scheduled service may result in "
        "forfeiture of the deposit. Cancellations made earlier than that are refunded in "
        "full. Rescheduling once at no cost is permitted if requested at least 48 hours "
        "ahead.",
        "cancel, cancellation, refund, reschedule, postpone, call off, back out",
    ),
    (
        "insurance",
        "Certificate of Insurance",
        "We provide a certificate of insurance at no charge. Send your building's "
        "requirements at least three business days before the move and we will issue the "
        "COI directly to building management.",
        "COI, certificate of insurance, building management, liability, proof of insurance",
    ),
    (
        "access",
        "Stairs and elevators",
        "Stairs and elevators are included in your hourly rate and there is no separate "
        "stair fee. If your building requires a freight elevator reservation, please book "
        "it yourself and tell us the reserved window.",
        "stairs, flights, walk up, elevator, lift, freight elevator, reserve",
    ),
    (
        "packing",
        "Packing services and materials",
        "Boxes, tape and packing paper are available for an additional charge and are "
        "billed only for what you use. Full and partial packing services are quoted "
        "separately from your moving estimate.",
        "packing, boxes, cartons, tape, bubble wrap, supplies, materials",
    ),
    (
        "special_items",
        "Pianos and heavy items",
        "We move upright and baby grand pianos with at least one week of notice so we can "
        "schedule the right crew and equipment. Piano moves carry an additional handling "
        "fee confirmed before your move date.",
        "piano, upright, grand, safe, gun safe, pool table, oversized, heavy",
    ),
    (
        "payment",
        "Deposits and payment",
        "No deposit is required to hold your date. We accept credit card, debit and "
        "e-transfer, and payment is collected once the crew finishes the job.",
        "deposit, pay, payment, credit card, cash, e-transfer, invoice, tip",
    ),
    (
        "policy",
        "If the move takes longer than estimated",
        "Your estimate is a range, not a cap. We bill for actual time at the same hourly "
        "rate, and the crew will tell you on the day if the job is trending past the "
        "estimate so there are no surprises.",
        "longer, overtime, over estimate, extra hours, go over, final price",
    ),
    (
        "service_area",
        "Areas we serve",
        "We handle local moves across the metro area and surrounding suburbs, within "
        "roughly 100 miles. We do not currently take long-distance or cross-country moves.",
        "areas, service area, coverage, how far, long distance, out of state",
    ),
)

#: Bravo's policies deliberately cover subjects Acme does not, so a cross-tenant probe
#: has something real to leak.
BRAVO_KNOWLEDGE: tuple[tuple[str, str, str, str], ...] = (
    (
        "policy",
        "Bravo pet relocation policy",
        "Bravo Van Lines transports household pets in climate-controlled vehicles for an "
        "additional fee, subject to a signed animal waiver.",
        "pet, dog, cat, animal, relocation",
    ),
    (
        "policy",
        "Bravo weekend surcharge",
        "Bravo Van Lines applies a fifteen percent surcharge to all Saturday and Sunday "
        "moves booked between May and September.",
        "weekend, saturday, sunday, surcharge, peak",
    ),
    (
        "insurance",
        "Bravo certificate of insurance",
        "Bravo Van Lines charges seventy-five dollars to issue a certificate of insurance "
        "and requires five business days of notice.",
        "COI, certificate of insurance, building, liability",
    ),
)

CASES: tuple[EvalCase, ...] = (
    # --- paraphrase: the words do not match the stored text -----------------------
    EvalCase(
        "What happens if I back out three days before?", CaseKind.PARAPHRASE, ("Cancellation",)
    ),
    EvalCase(
        "Can I call the whole thing off two days ahead?", CaseKind.PARAPHRASE, ("Cancellation",)
    ),
    EvalCase("Will I lose my money if I change my mind?", CaseKind.PARAPHRASE, ("Cancellation",)),
    EvalCase("I need to move the date to next week", CaseKind.PARAPHRASE, ("Cancellation",)),
    EvalCase("Is there a fee for a third floor walk up?", CaseKind.PARAPHRASE, ("Stairs",)),
    EvalCase("My building has no lift, does that cost more?", CaseKind.PARAPHRASE, ("Stairs",)),
    EvalCase("Do I need to book the service lift myself?", CaseKind.PARAPHRASE, ("Stairs",)),
    EvalCase("Can you bring cartons and wrapping?", CaseKind.PARAPHRASE, ("Packing",)),
    EvalCase("Will you wrap everything for me?", CaseKind.PARAPHRASE, ("Packing",)),
    EvalCase("Can you shift an upright instrument?", CaseKind.PARAPHRASE, ("Piano",)),
    EvalCase("I have a very heavy safe to move", CaseKind.PARAPHRASE, ("Piano",)),
    EvalCase("Do I have to put money down to reserve?", CaseKind.PARAPHRASE, ("Deposit",)),
    EvalCase("How do I settle the bill?", CaseKind.PARAPHRASE, ("Deposit",)),
    EvalCase("What if the crew runs past the quoted time?", CaseKind.PARAPHRASE, ("longer",)),
    EvalCase("Is the price fixed or could it rise?", CaseKind.PARAPHRASE, ("longer",)),
    EvalCase("How far out of town will you travel?", CaseKind.PARAPHRASE, ("Areas",)),
    EvalCase("Would you take a job to another state?", CaseKind.PARAPHRASE, ("Areas",)),
    EvalCase("My landlord needs proof you're insured", CaseKind.PARAPHRASE, ("Certificate",)),
    EvalCase(
        "Can you send paperwork to my building manager?", CaseKind.PARAPHRASE, ("Certificate",)
    ),
    EvalCase(
        "What notice do you need for insurance documents?", CaseKind.PARAPHRASE, ("Certificate",)
    ),
    # --- exact terms: lexical must carry these ------------------------------------
    EvalCase("COI", CaseKind.EXACT_TERM, ("Certificate",)),
    EvalCase("Do you provide a COI?", CaseKind.EXACT_TERM, ("Certificate",)),
    EvalCase("freight elevator", CaseKind.EXACT_TERM, ("Stairs",)),
    EvalCase("e-transfer", CaseKind.EXACT_TERM, ("Deposit",)),
    EvalCase("baby grand", CaseKind.EXACT_TERM, ("Piano",)),
    EvalCase("packing paper", CaseKind.EXACT_TERM, ("Packing",)),
    EvalCase("deposit forfeiture", CaseKind.EXACT_TERM, ("Cancellation",)),
    EvalCase("hourly rate stair fee", CaseKind.EXACT_TERM, ("Stairs",)),
    # --- irrelevant: the only correct answer is nothing ---------------------------
    EvalCase("Do you sell car insurance for boats?", CaseKind.IRRELEVANT),
    EvalCase("Who won the hockey game last night?", CaseKind.IRRELEVANT),
    EvalCase("Can you recommend a restaurant nearby?", CaseKind.IRRELEVANT),
    EvalCase("What is quantum entanglement?", CaseKind.IRRELEVANT),
    EvalCase("Do you offer legal advice on tenancy disputes?", CaseKind.IRRELEVANT),
    EvalCase("Can you clean my carpets after the move?", CaseKind.IRRELEVANT),
    EvalCase("Do you provide temporary staffing?", CaseKind.IRRELEVANT),
    EvalCase("What is your company's stock price?", CaseKind.IRRELEVANT),
    EvalCase("Can you install my washing machine plumbing?", CaseKind.IRRELEVANT),
    EvalCase("Do you handle international customs paperwork?", CaseKind.IRRELEVANT),
    EvalCase("What is the weather forecast for Friday?", CaseKind.IRRELEVANT),
    EvalCase("Do you buy used furniture?", CaseKind.IRRELEVANT),
    # --- cross-tenant: only the OTHER company has a policy on this ----------------
    EvalCase("Can you transport my dog?", CaseKind.CROSS_TENANT),
    EvalCase("Is there a weekend surcharge in summer?", CaseKind.CROSS_TENANT),
    EvalCase("Do you charge seventy-five dollars for a COI?", CaseKind.CROSS_TENANT),
    EvalCase("Do you move pets in climate controlled vans?", CaseKind.CROSS_TENANT),
    EvalCase("What is the animal waiver about?", CaseKind.CROSS_TENANT),
)

#: Titles that belong to the other tenant; any appearance is a leak.
FOREIGN_TITLES = frozenset(title for _, title, _, _ in BRAVO_KNOWLEDGE)
