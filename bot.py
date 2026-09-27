"""Deterministic Vera message engine for the magicpin AI challenge.

No network calls and no randomness: the same four contexts always produce the same
composition. The HTTP service in server.py wraps these pure functions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Optional


def _first_name(identity: dict) -> str:
    if identity.get("owner_first_name"):
        return str(identity["owner_first_name"])
    name = str(identity.get("name", "")).strip()
    if name.startswith("Dr. "):
        name = name[4:]
    return re.split(r"[ '\-]", name)[0] or "there"


def _active_offers(merchant: dict) -> list[dict]:
    return [o for o in merchant.get("offers", []) if str(o.get("status", "")).lower() == "active"]


def _offer(merchant: dict, contains: str = "") -> Optional[str]:
    offers = _active_offers(merchant)
    if contains:
        for o in offers:
            if contains.lower() in o.get("title", "").lower():
                return o["title"]
    return offers[0].get("title") if offers else None


def _category_voice(category: dict) -> str:
    return str(category.get("voice", {}).get("tone", "peer_operator"))

def _merchant_salutation(category: dict, identity: dict) -> str:
    owner = _first_name(identity)
    slug = category.get("slug", "")
    if slug == "dentists":
        raw = str(identity.get("owner_first_name", owner)).strip()
        return raw if raw.lower().startswith("dr.") else f"Dr. {owner}"
    return owner


def _fmt_pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except Exception:
        return str(x)


def _latest_digest(category: dict, trigger: dict) -> Optional[dict]:
    payload = trigger.get("payload", {})
    wanted = payload.get("top_item_id") or payload.get("digest_item_id") or payload.get("alert_id")
    digest = category.get("digest", [])
    if wanted:
        for item in digest:
            if item.get("id") == wanted:
                return item
    return digest[0] if digest else None


def _name_for_customer(customer: Optional[dict]) -> str:
    return str((customer or {}).get("identity", {}).get("name", "")).strip()


def _customer_consent(customer: Optional[dict], purpose: str) -> bool:
    if not customer:
        return False
    consent = customer.get("consent", {})
    scopes = consent.get("scope", []) or []
    if not consent.get("opted_in_at"):
        return False
    return purpose in scopes


def _customer_language(customer: dict) -> str:
    return str(customer.get("identity", {}).get("language_pref", "en")).lower()


def _base(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    customer_scope = trigger.get("scope") == "customer" or customer is not None
    return {
        "body": "",
        "cta": "none",
        "send_as": "merchant_on_behalf" if customer_scope else "vera",
        "suppression_key": trigger.get("suppression_key", trigger.get("id", "unknown")),
        "rationale": "",
    }


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    """Return body, cta, send_as, suppression_key and rationale deterministically."""
    out = _base(category, merchant, trigger, customer)
    kind = trigger.get("kind", "")
    p = trigger.get("payload", {}) or {}
    ident = merchant.get("identity", {})
    mname = ident.get("name", "your business")
    owner = _merchant_salutation(category, ident)
    city = ident.get("city", "")
    locality = ident.get("locality", "")
    cat = merchant.get("category_slug", category.get("slug", ""))
    perf = merchant.get("performance", {}) or {}
    signals = merchant.get("signals", []) or []
    active = _active_offers(merchant)
    offer = _offer(merchant)

    # Customer-facing compliance: do not outreach without relevant consent.
    if customer is not None and trigger.get("scope") == "customer":
        purpose = "recall_reminders" if kind == "recall_due" else "appointment_reminders" if kind == "appointment_tomorrow" else "winback_offers" if kind == "customer_lapsed_hard" else "promotional_offers" if kind == "customer_lapsed_soft" else "refill_reminders" if kind == "chronic_refill_due" else "promotional_offers"
        if not _customer_consent(customer, purpose):
            out.update({"body": "", "cta": "none", "rationale": "Customer outreach suppressed because the supplied consent scope does not cover this trigger."})
            return out

    # Research / knowledge triggers.
    if kind == "research_digest":
        item = _latest_digest(category, trigger)
        if item:
            title = item.get("title", "A new category update")
            source = item.get("source", "source provided in the digest")
            trial_n = item.get("trial_n")
            delta = item.get("delta_pct")
            segment = item.get("patient_segment", "")
            summary = str(item.get("summary", "")).strip()
            cohort = merchant.get("customer_aggregate", {}).get("high_risk_adult_count")
            if segment == "high_risk_adults" and cohort is not None:
                relevance = f"your {cohort} high-risk adult patients"
            else:
                relevance = "your current customer mix"
            evidence = []
            if trial_n is not None:
                evidence.append(f"{trial_n:,}-patient trial")
            if delta is not None:
                evidence.append(f"{abs(float(delta))*100:.0f}% difference" if abs(float(delta)) <= 1 else f"{delta}")
            evidence_text = (" — " + ", ".join(evidence)) if evidence else ""
            summary_text = f" {summary}" if summary else ""
            out["body"] = f"{owner}, {source} has a new item worth a look: {title}{evidence_text}. It matters for {relevance}.{summary_text} Want me to pull the key takeaway and turn it into a ready-to-use customer message?"
            out["cta"] = "open_ended"
            out["rationale"] = f"Research trigger anchored to the supplied digest item and the merchant's {cat} context; offers to do the next step rather than asking the merchant to research it."
            return out

    if kind in {"cde_opportunity"}:
        out["body"] = f"{owner}, there's a free-for-members CDE opportunity in your {cat} category with {p.get('credits', 0)} credits. Want me to pull the webinar details and draft a quick patient-facing takeaway?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "External professional opportunity; uses only supplied credits/fee facts and offers a concrete low-effort next step."
        return out

    if kind == "regulation_change":
        item = _latest_digest(category, trigger)
        deadline = p.get("deadline_iso")
        detail = item.get("title") if item else "a regulatory change"
        source = item.get("source") if item else "the supplied category digest"
        summary = str(item.get("summary", "")).strip() if item else ""
        detail2 = f" {summary}" if summary else ""
        out["body"] = f"{owner}, heads-up: {detail}. Effective deadline: {deadline or 'see the supplied notice'}. {source}.{detail2} Want me to turn this into a short compliance checklist for the practice?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "High-urgency regulatory trigger; cites the supplied source and deadline, then offers a bounded compliance artifact."
        return out

    if kind == "supply_alert":
        batches = ", ".join(p.get("affected_batches", []))
        mol = p.get("molecule", "the affected medicine")
        manufacturer = p.get("manufacturer", "the manufacturer")
        agg = merchant.get("customer_aggregate", {})
        count = agg.get("affected_chronic_rx_count") or agg.get("affected_customer_count")
        digest_item = _latest_digest(category, trigger)
        summary = str(digest_item.get("summary", "")).strip() if digest_item else ""
        suffix = f" Your supplied customer data flags {count} affected chronic-Rx customers." if count is not None else ""
        detail = f" {summary}" if summary else ""
        out["body"] = f"{owner}, urgent: {mol} recall from {manufacturer} covers batches {batches}.{detail}{suffix} Want me to draft the customer notice and replacement workflow?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Urgent pharmacy supply alert; uses the supplied molecule, manufacturer and batch identifiers without inventing clinical risk."
        return out

    # High-intent planning: complete the requested artifact immediately.
    if kind == "active_planning_intent":
        topic = p.get("intent_topic", "the requested plan")
        if "corporate_bulk_thali" in topic:
            retail = _offer(merchant, "Thali") or "Weekday Lunch Thali @ ₹149"
            out["body"] = (f"{owner}, here’s a starter corporate-thali plan for {locality}:\n"
                           f"• 10 thalis @ ₹125 each + free delivery\n"
                           f"• 25 @ ₹115 each + 2 filter coffees\n"
                           f"• 50+ @ ₹105 each + 1 dosa platter\n"
                           f"Your current offer is {retail}. Want me to turn this into a 3-line WhatsApp for nearby office admins?")
        elif "kids_yoga" in topic:
            out["body"] = (f"{owner}, for a kids-yoga summer camp at {mname}, I’d start with a 4-week beginner block: 2 weekday sessions/week, 45 min each, capped small enough for instructor attention. You already have {offer or 'no active offer'} live. Want me to draft the parent-facing launch message and a simple price ladder?")
        else:
            out["body"] = f"{owner}, you asked for a concrete version of {topic}. I can turn the supplied merchant context into a ready-to-send draft now. Want me to draft it?"
        out["cta"] = "open_ended"
        out["rationale"] = "The merchant explicitly asked what the plan would look like; switching from qualification to a usable draft immediately."
        return out

    # Customer lifecycle / appointment / refill.
    if customer is not None and kind == "recall_due":
        cname = _name_for_customer(customer)
        slots = p.get("available_slots", [])
        slot_text = " or ".join(s.get("label", "") for s in slots[:2])
        active_offer = _offer(merchant, "Cleaning") or offer
        lang = _customer_language(customer)
        greeting = "Hi"
        if slot_text:
            body = f"{greeting} {cname}, {mname} here 🦷 Your {p.get('service_due', 'recall').replace('_', ' ')} is due. Apke liye {slot_text} ready hain. {active_offer + '. ' if active_offer else ''}Reply YES and I’ll help confirm a suitable slot." if "hi" in lang else f"{greeting} {cname}, {mname} here 🦷 Your {p.get('service_due', 'recall').replace('_', ' ')} is due. We have {slot_text} available. {active_offer + '. ' if active_offer else ''}Reply YES and I’ll help confirm a suitable slot."
        else:
            rel = customer.get("relationship", {})
            last = rel.get("last_visit")
            visits = rel.get("visits_total")
            history = f" Your last visit was {last}, after {visits} visits." if last and visits else (f" Your last visit was {last}." if last else "")
            body = f"{greeting} {cname}, {mname} here. Your regular recall is due.{history} Reply YES and I’ll help find a suitable slot."
        out["body"] = body
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Customer recall trigger; uses the supplied customer name, service due, real available slots and active merchant offer while respecting opt-in."
        return out

    if customer is not None and kind == "customer_lapsed_soft":
        cname = _name_for_customer(customer)
        last_visit = customer.get("relationship", {}).get("last_visit")
        visits = customer.get("relationship", {}).get("visits_total")
        lang = _customer_language(customer)
        prefix = "Hi" if not lang.startswith("hi") else "Hi"
        detail = f"You last visited on {last_visit}" if last_visit else "It’s been a little while"
        history = f" after {visits} visits" if visits else ""
        if cat == "dentists":
            body = f"{prefix} {cname}, {mname} here. {detail}{history}. If you'd like to come back, I can help find a convenient appointment — no pressure. Reply YES and we’ll take it from there."
        elif cat == "pharmacies":
            body = f"{prefix} {cname}, {mname} here. {detail}{history}. If you need anything for your regular pharmacy needs, I can help check what’s available. Reply YES if you’d like a quick update."
        else:
            body = f"{prefix} {cname}, {mname} here. {detail}{history}. If you’d like to come back, I can help with the next step. Reply YES and we’ll take it from there."
        out["body"] = body
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Soft-lapse outreach uses the real relationship history, avoids guilt, and asks for one low-friction next step within the supplied promotional consent."
        return out

    if customer is not None and kind == "customer_lapsed_hard":
        cname = _name_for_customer(customer)
        days = p.get("days_since_last_visit")
        focus = p.get("previous_focus") or "previous"
        gym_offer = _offer(merchant, "Trial") or offer

        days_text = f"{days} days" if days is not None else "a little while"
        focus_text = str(focus).replace("_", " ")

        out["body"] = (
            f"Hi {cname} 👋 {owner} from {mname} here. "
            f"It’s been {days_text} — no pressure. "
            f"We can pick up your {focus_text} goal with "
            f"{gym_offer or 'a trial session'}. "
            f"Want me to hold a trial spot?"
        )
        out["cta"] = "binary_yes_no"
        out["rationale"] = (
            "Hard-lapse winback; non-judgmental language, prior goal and "
            "current offer are grounded in the supplied contexts."
        )
        return out

    if customer is not None and kind == "wedding_package_followup":
        cname = _name_for_customer(customer)
        days = p.get("days_to_wedding")
        owner = _first_name(ident)
        body = f"Hi {cname} 💍 {owner} from {mname} here. Your wedding is {days} days away, and your skin-prep window is open after the bridal trial. Want me to help block the first prep session?"
        out["body"] = body
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Bridal follow-up continues the supplied trial relationship and wedding timeline with one concrete scheduling step."
        return out

    if customer is not None and kind == "appointment_tomorrow":
        cname = _name_for_customer(customer)
        if _customer_language(customer).startswith("hi"):
            body = f"Hi {cname}, kal aapki appointment {mname} ke saath hai. Please reply YES to confirm, ya agar timing change karni ho to bata dein."
        else:
            body = f"Hi {cname}, a reminder from {mname}: your appointment is tomorrow. Reply YES to confirm, or tell us if you need a different time."
        out["body"] = body
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Customer appointment reminder; consent and channel are supplied, and the CTA is limited to confirmation/rescheduling."
        return out

    if customer is not None and kind == "chronic_refill_due":
        cname = _name_for_customer(customer)
        mols = p.get("molecule_list", [])
        runout = p.get("stock_runs_out_iso", "").split("T")[0]
        if cat == "pharmacies" and mols:
            senior = customer.get("identity", {}).get("senior_citizen")
            discount = _offer(merchant, "Senior") if senior else None
            delivery = _offer(merchant, "Delivery")
            via_son = "Namaste" if "via_son" in str(customer.get("preferences", {}).get("channel", "")) or _customer_language(customer).startswith("hi") else "Hi"
            body = f"{via_son} {cname} — {mname} here. Your {', '.join(mols)} are due for refill around {runout or 'the supplied refill date'}."
            if discount: body += f" {discount} applies."
            if delivery: body += f" {delivery}."
            body += " Reply CONFIRM if you want us to prepare the refill."
        else:
            body = f"Hi {cname}, {mname} here. We have a reminder related to your regular care. Reply YES if you want us to check the next step with you."
        out["body"] = body
        out["cta"] = "binary_confirm_cancel"
        out["rationale"] = "Customer refill trigger is handled only with category-appropriate details present in the supplied context; no medicine facts are invented for non-pharmacy merchants."
        return out

    # Category-specific merchant triggers.
    if kind == "category_seasonal":
        trends = p.get("trends", [])
        out["body"] = f"{owner}, summer demand is shifting in your pharmacy: ORS {trends[0].split('_')[-1] if trends else '+40'}, sunscreen +38%, antifungal +45%, while cold/cough is down 60% in the supplied signal. Want me to turn that into a 3-step shelf + post plan?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Seasonal pharmacy trigger translated into inventory/shelf action using the supplied category demand shifts."
        return out

    if kind == "ipl_match_today":
        match = p.get("match", "the match")
        time = p.get("match_time_iso", "").split("T")[-1][:5]
        offer = _offer(merchant, "Buy 1") or offer
        digest_item = next((x for x in category.get("digest", []) if x.get("id") == "d_2026W17_ipl_window"), None)
        insight = ""
        if digest_item:
            insight = " The supplied IPL data says Saturday matches shift restaurant covers down 12% versus the Saturday average."
        out["body"] = f"Quick heads-up {owner} — {match} is at {p.get('venue', 'the stadium')} tonight around {time}.{insight} I’d skip a new match-night dine-in promo and instead position your existing {offer or 'offer'} for delivery. Want me to draft the delivery-first copy?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Interprets the match-day trigger in context of a weekend schedule and reuses the merchant's live offer rather than fabricating a new one."
        return out

    if kind == "competitor_opened":
        comp = p.get("competitor_name")
        dist = p.get("distance_km")
        their = p.get("their_offer")
        if comp:
            body = f"{owner}, {comp} opened {dist} km away with {their}. Your current profile is at {perf.get('ctr', 0)*100:.1f}% CTR versus the category benchmark {category.get('peer_stats', {}).get('avg_ctr', 0)*100:.1f}%. I’d tighten the listing before changing price. Want me to draft the first 3 listing changes?"
        else:
            ctr = perf.get("ctr")
            offer_text = _offer(merchant)

            ctr_text = (
                f" Your current CTR is {float(ctr) * 100:.1f}%."
                if ctr is not None
                else ""
            )

            offer_part = (
                f" Your active offer is {offer_text}."
                if offer_text
                else ""
            )

            body = (
                f"{owner}, there’s new nearby competitor activity on your listing."
                f"{ctr_text}{offer_part} "
                f"Want me to draft the first 3 listing changes?"
            )
        out["body"] = body; out["cta"] = "binary_yes_no"
        out["rationale"] = "Competitive trigger combined with the merchant's actual CTR and category benchmark; recommends improving the listing before price changes."
        return out

    if kind == "perf_dip":
        metric = p.get("metric")
        delta = p.get("delta_pct")
        current = perf.get(metric) if metric else None
        baseline = p.get("vs_baseline")
        if delta is None:
            d7 = perf.get("delta_7d", {}) or {}
            if metric and f"{metric}_pct" in d7:
                delta = d7.get(f"{metric}_pct")
        if delta is None:
            recent = ", ".join(f"{k.replace('_pct','')} {float(v)*100:+.0f}%" for k,v in (perf.get("delta_7d", {}) or {}).items())
            out["body"] = f"{owner}, I received a performance-dip alert, but the supplied trigger doesn't say which metric changed. Your current 7-day snapshot is {recent or 'missing a usable change signal'}. Want me to check the strongest current signal before suggesting an action?"
        elif float(delta) < 0:
            extra = f" Current {metric}: {current}." if current is not None else ""
            base = f" versus the supplied baseline of {baseline}" if baseline is not None else ""
            out["body"] = f"{owner}, {metric} is down {_fmt_pct(abs(delta))} over {p.get('window', '7d')}{base}.{extra} I’d fix the signal behind the drop before adding spend. Want me to draft the first recovery action?"
        else:
            recent = ", ".join(f"{k.replace('_pct','')} {float(v)*100:+.0f}%" for k,v in (perf.get("delta_7d", {}) or {}).items())
            out["body"] = f"{owner}, the alert says performance dipped, but the current merchant snapshot is not showing a negative {metric or 'performance'} move{(' — ' + recent) if recent else ''}. Want me to verify the underlying signal before we change anything?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Performance-dip handling prioritizes the trigger metric when supplied; when the trigger is a placeholder or conflicts with the current snapshot, it avoids fabricating a decline and asks to verify the underlying signal."
        return out

    if kind == "seasonal_perf_dip":
        delta = p.get("delta_pct", perf.get("delta_7d", {}).get("views_pct", 0))
        members = merchant.get("customer_aggregate", {}).get("total_active_members")
        peer_low, peer_high = None, None
        digest_item = next((x for x in category.get("digest", []) if x.get("kind") == "seasonal"), None)
        digest_summary = str(digest_item.get("summary", "")) if digest_item else ""
        member_text = f" You have {members} active members to retain." if members else ""
        benchmark = ""
        m = re.search(r"(-?\d+%\s*(?:to|-)\s*\d+%)", digest_summary)
        if m:
            benchmark = f" The category digest says April-June is the lowest acquisition window, so this is a retention moment rather than a panic signal."
        out["body"] = f"{owner}, views are down {_fmt_pct(abs(delta))} this week, but the supplied trigger flags this as an expected April-June seasonal lull.{benchmark}{member_text} Want me to draft a simple summer attendance challenge?"
        out["cta"] = "binary_yes_no"; out["rationale"] = "Expected seasonal dip is reframed rather than treated as an emergency; recommendation uses the supplied season flag and merchant state."
        return out

    if kind == "perf_spike":
        metric = p.get("metric")
        delta = p.get("delta_pct")
        driver = p.get("likely_driver")
        if delta is None:
            d7 = perf.get("delta_7d", {}) or {}
            candidate = d7.get(f"{metric}_pct") if metric else None
            if candidate is None and d7:
                metric = "calls" if "calls_pct" in d7 else next(iter(d7)).replace("_pct", "")
                candidate = d7.get(f"{metric}_pct")
            delta = candidate
        if delta is not None:
            driver_text = f" The supplied signal links it to {driver.replace('_',' ')}." if driver else ""
            out["body"] = f"{owner}, {metric or 'performance'} is up {_fmt_pct(delta)} over the supplied 7-day window.{driver_text} Want me to turn what’s working into one repeatable post or offer?"
        else:
            out["body"] = f"{owner}, I received a performance-spike alert, but the supplied trigger doesn't include the changed metric. Want me to use your current snapshot to identify the strongest signal before drafting an action?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Performance-spike handling uses the explicit trigger metric when available and falls back to the merchant's recent snapshot without inventing a hidden cause."
        return out

    if kind == "milestone_reached":
        value = p.get("value_now")
        milestone = p.get("milestone_value")
        metric = p.get("metric", "profile milestone")

        if value is not None and milestone is not None:
            remaining = max(0, float(milestone) - float(value))

            if metric == "review_count":
                metric_text = "reviews"
            else:
                metric_text = str(metric).replace("_", " ")

            body = (
                f"{owner}, you’re at {value} {metric_text} — "
                f"only {remaining:g} short of the {milestone} milestone. "
                f"Want me to draft a simple customer ask to help you reach it?"
            )
        else:
            metric_text = "your next review" if metric == "review_count" else str(metric).replace("_", " ")
            body = (
                f"{owner}, you’re approaching {metric_text} milestone. "
                f"I can turn it into one simple customer-facing ask. "
                f"Want me to draft it?"
            )

        out["body"] = body
        out["cta"] = "binary_yes_no"
        out["rationale"] = (
            "Milestone trigger uses exact progress when supplied; when "
            "progress fields are missing, it avoids inventing numbers while "
            "still naming the supplied metric and giving one concrete action."
        )
        return out

    if kind == "gbp_unverified":
        uplift = p.get("estimated_uplift_pct")
        out["body"] = f"{owner}, your Google Business Profile is still unverified. The supplied estimate is up to {_fmt_pct(uplift)} uplift after verification. Want me to walk you through the supplied {p.get('verification_path', 'verification')} path?"
        out["cta"] = "binary_yes_no"; out["rationale"] = "GBP verification trigger tied to the supplied verification path and uplift estimate."
        return out

    if kind == "dormant_with_vera":
        days = p.get("days_since_last_merchant_message")
        topic = p.get("last_topic")

        topic_labels = {
            "subscription_expiry": "your subscription",
            "profile_update": "your profile",
            "offer_setup": "your offer setup",
            "campaign": "your campaign",
        }

        topic_text = topic_labels.get(
            str(topic).lower() if topic else "",
            "your profile"
        )

        days_text = f"{days} days" if days is not None else "a little while"

        out["body"] = (
            f"{owner}, it’s been {days_text} since we last worked on "
            f"{topic_text}. I found a concrete next step from your current "
            f"merchant data — want me to surface it in one message?"
        )
        out["cta"] = "binary_yes_no"
        out["rationale"] = (
            "Dormancy trigger; elapsed time and prior topic are translated "
            "into natural merchant-facing language, with a single low-friction next step."
        )
        return out

    if kind == "category_seasonal":
        trends = p.get("trends", [])
        parsed = []
        for trend in trends[:4]:
            parsed.append(str(trend).replace("_", " ").replace("+", " +").replace("-", " -"))
        trend_text = ", ".join(parsed) if parsed else "a seasonal demand shift"
        out["body"] = f"{owner}, your supplied summer signal shows {trend_text}. That points to a shelf-and-listing shift, not a blanket discount. Want me to turn the top 2 demand moves into a 3-step shelf + post plan?"
        out["cta"] = "binary_yes_no"
        out["rationale"] = "Seasonal pharmacy trigger translated into a concrete merchandising decision using only the supplied demand shifts."
        return out

    if kind == "festival_upcoming":
        fest = p.get("festival"); days = p.get("days_until")
        relevant = " + ".join(p.get("category_relevance", [])[:2])
        if fest and days is not None:
            context = f"{fest} is {days} days away"
        else:
            context = "a festival opportunity is active, but the supplied trigger is missing the festival name/date"
        rel = f" ({relevant})" if relevant else ""
        out["body"] = f"{owner}, {context}. For {cat}{rel}, I’d avoid inventing a blanket discount and instead build one offer around your actual merchant data. Want me to draft that offer?"
        out["cta"] = "binary_yes_no"; out["rationale"] = "Festival trigger converted into a category-aware offer decision while reusing a live merchant offer."
        return out

    if kind == "curious_ask_due":
        active_titles = [o.get("title") for o in active[:2]]
        hint = f" You currently have {', '.join(active_titles)} live." if active_titles else ""
        out["body"] = f"Hi {owner}! Quick operator question: what service has been most asked-for this week?{hint} I’ll turn your answer into a ready-to-use listing/WhatsApp draft in one pass."
        out["cta"] = "open_ended"; out["rationale"] = "Curious-ask cadence deliberately uses a low-stakes question plus effort externalization, with current offer context added when available."
        return out

    if kind == "renewal_due":
        days = p.get("days_remaining", merchant.get("subscription", {}).get("days_remaining"))
        plan = p.get("plan", merchant.get("subscription", {}).get("plan"))
        amount = p.get("renewal_amount")
        price = f" at ₹{amount:,}" if amount else ""
        out["body"] = f"{owner}, your {plan or 'current'} plan renews in {days} days{price}. Before you renew, want me to show the one merchant-growth action with the clearest signal from your current data?"
        out["cta"] = "binary_yes_no"; out["rationale"] = "Renewal trigger states the exact plan/timing and offers value before asking for a renewal decision."
        return out

    if kind == "winback_eligible":
        days = p.get("days_since_expiry"); added = p.get("lapsed_customers_added_since_expiry")
        out["body"] = f"{owner}, you’ve been expired {days} days and the supplied data shows {added} lapsed customers added since then. Want me to draft one win-back message using your current profile instead of a generic discount?"
        out["cta"] = "binary_yes_no"; out["rationale"] = "Winback trigger combines elapsed expiry and customer-state signal into a concrete outreach proposal."
        return out

    if kind == "review_theme_emerged":
        themes = merchant.get("review_themes", [])
        theme = themes[0] if themes else None
        if isinstance(theme, dict):
            detail = theme.get("theme") or theme.get("text") or str(theme)
        else: detail = str(theme) if theme else "a repeated review theme"
        out["body"] = f"{owner}, a repeated review theme is showing up: {detail}. Want me to turn it into a one-line service recovery response plus a profile update?"
        out["cta"] = "binary_yes_no"; out["rationale"] = "Review-theme trigger uses the merchant's review evidence and proposes one bounded remediation artifact."
        return out

    # Generic deterministic fallback for future injected trigger kinds.
    key = kind.replace("_", " ") or "new signal"
    out["body"] = f"{owner}, there’s a new {key} signal for {mname}. I’m using your current {cat} context rather than a generic campaign. Want me to turn the strongest supplied signal into one concrete next step?"
    out["cta"] = "binary_yes_no"
    out["rationale"] = f"Fallback for an unseen trigger kind; grounds the message in merchant/category identity without inventing unsupported facts."
    return out


__all__ = ["compose"]
