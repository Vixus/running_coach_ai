# PM Critique — AI Running Coach

**Date:** 2026-05-14
**Reviewer:** Claude (Opus 4.7), product-manager lens
**Context:** Standalone critique requested mid-session after a timezone bugfix; product is on branch `006-magazine-features`.

---

## What's working

**The differentiated wedge is real.** Adaptive plans that read HRV/body-battery each morning and write workouts back to the watch is genuinely better than the static-template world (TrainingPeaks, Garmin Coach, generic plans). Closing the loop — *health → plan adjustment → watch → completed run → feedback* — is the thing most "AI coach" apps gesture at but don't ship. You shipped it.

**Conversational adjustment is the right interaction model.** "I'm sore, swap tomorrow" beats every drag-and-drop calendar UI. The five XML side-effect tags (`<plan>`, `<garmin_sync/>`, `<remember>`, `<switch_prescription>`, `<coach_switch>`) are a clean architecture for letting the coach *do things*, not just talk.

**Personas + Story are smart identity moves.** Most fitness apps treat the coach as anonymous infrastructure. Naming Alex/Maya/Jordan with distinct philosophies, and turning training into a magazine editorial, build emotional attachment that pure metrics dashboards never get. Strava has kudos; you have narrative. That's a defensible retention lever competitors won't copy quickly.

**Morning check-in as habit anchor** is the right daily ritual. HRV-gated polling (don't fire until data lands; skip after noon) is the kind of detail that separates "demo-ware" from a product someone actually uses every day.

---

## What's not working

**Garmin-only kills your TAM.** Apple Watch, Coros, Polar, Suunto, and Strava-as-aggregator users are locked out — that's probably 60–70% of serious runners. Strava's universal pipe + Apple HealthKit are the obvious next integrations; even read-only Strava would unlock a huge audience.

**Asking for Garmin password + disabled MFA is a trust cliff.** "Give me your Garmin credentials and turn off 2FA" reads like phishing to a security-aware user, and the encrypted-at-rest story doesn't help in the *trust* moment — only post-hoc. Garmin's OAuth flow (via Garmin Connect Developer / FIT API) or pivoting to read-only Strava OAuth fixes this. Until then, you're filtering for users who don't think hard about credential hygiene, which is anti-correlated with "serious data-driven runner."

**Self-hosted + Docker + NAS is a 99% audience filter.** Brilliant for v0 dogfooding; fatal as a product. Even technical runners don't want to babysit Garmin token refreshers running on residential IPs. If this is hobby/personal: fine, ignore. If it's a product: you need a managed instance with one-click signup, and the hosting model needs to absorb the Garmin-token-refresh fragility, not the user.

**Mobile gap is the biggest UX flaw.** Runners check their phone at 6am while still in bed. A web dashboard means they have to open a browser, log in, navigate. Native push (or even PWA + web push) for the morning check-in and post-run feedback is table stakes. The notification bell that only polls while the tab is open is essentially invisible.

**The magazine dashboard is gorgeous but inverted.** Pre-run, a runner has one question: *"What am I running today, what pace, why?"* Right now that lives under a hero, terrain SVG, glamor headlines, then morning section. The Bebas Neue typography and editorial layout are stunning — but they treat the app as a *coffee-table magazine* rather than a *running tool*. Consider a "today" first-class card at top, then the magazine experience below for browsing. Speed-to-answer matters more than aesthetic on a daily-use surface.

> **NOTE (2026-05-14):** This is being addressed directly in spec 007 (Today Card / Magazine Cover hybrid).

**Story feature has a reward-cycle problem.** Triggers fire on race_complete, PR_set, race_upcoming, etc. — these are rare. A new user opts in, sees the intro modal, then... nothing for weeks until a milestone hits. The opt-in adds friction without an immediate payoff. Consider: a "founding chapter" story generated from the onboarding intake itself, so the user has *something* the day they sign up, and the milestone-triggered chapters extend it.

**Persona switching risks eroding trust.** If I switch from Alex to Maya mid-block and the plan shifts, I don't know if "my coach changed her mind" or "I changed coaches." Either commit to "this is your coach, pick once," or make persona differences cosmetic (voice, framing) while keeping the plan math identical — but don't have them disagree on workouts without warning.

**No "why" surfaced for plan changes.** The coach adapts based on HRV/sleep, but unless the chat message spells it out, the athlete sees a workout move from tempo to easy and doesn't know whether that's the coach being smart or random. Every adaptation needs a visible because-of-X attribution. (You may have this in `coach_analysis` — if so, the dashboard isn't surfacing it loudly enough.)

**No outcome surfacing.** What's the *receipt* for using this app for 12 weeks? "Your tempo pace dropped 27 sec/mi" should be on the home screen, prominently, not buried in an 8-week history chart. Runners need to feel they got faster *because of* the coach. This is also your testimonial generator.

**No social/peer layer.** Strava is sticky because of kudos, not training data. The Story share-token gestures at sharing but there's no feed, no follow, no peer comparison. You don't need to ship Strava — but a "share this week with my run club" mechanism would compound.

---

## The strategic questions

1. **Who is this for, precisely?** "Experienced runner with Garmin + technical comfort + race goal" is ~5% of runners. Worth narrowing further (e.g., marathon-training first-timers who want pro-level guidance without a $200/mo coach) and saying no to everyone else.
2. **What's the daily action?** Morning check-in is the candidate. It needs push notification, sub-15-second time-to-value, and one big "today's workout" answer.
3. **What kills you in month 3?** Garmin auth fragility. Most users will have *one* week where tokens break, MFA gets re-enabled, or Garmin 429s for two days. Plan for graceful degradation that doesn't read as "broken app."
4. **Where's the business model?** Self-hosted + BYO Anthropic key is the hobbyist arc. The PM version of this product is hosted, $15–25/mo, and competes with Runna/Humango/Whoop Coach. Pick a lane.

---

## If I had to pick three things to ship next

1. **Strava read-only integration** — unlocks 5–10× the user base overnight; sidesteps the Garmin-password problem.
2. **Mobile push for morning check-in** — even via PWA; the daily habit dies without it.
3. **"Today" card pinned to the top of the dashboard** with workout + pace + one-sentence rationale ("Easy 5mi @ 9:40 — your HRV is down 4 from baseline, recovering from Saturday's long run"). Magazine below. → **In progress as spec 007.**

---

Overall: the architecture and instincts are *better* than 90% of consumer fitness apps I've seen. The execution gaps are mostly distribution and habit-loop, not the coaching engine — which is the hard part you've already solved.
