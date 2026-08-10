// widenNote — the single source of the "closest shops are within X km" copy.
//
// The Trade feed keeps a tight proximity radius by default (the moat: "people next door"). When the
// buyer's immediate radius holds fewer than one page of local content the backend widens ONCE to
// pull in the nearest content and flags it (FeedResponse.widened + nearest_distance_m +
// immediate_count). Both surfaces — the Listings timeline (ProductFeed) and the Videos strip/overlay
// (TradePage) — show the SAME honest note built here, so the two lanes can never drift in wording.
//
// HONESTY: the note reports distance ONLY. The platform has no delivery/fulfilment capability, so it
// must never claim "delivery is available" — that would assert a feature that doesn't exist (same
// contract as "Confirmed = provenance, not safety"). Distance is the truthful signal. And the copy
// must not claim the area is EMPTY when it isn't: `immediate_count` splits the two cases, because the
// widen now fires on a merely-sparse radius (some local items) as well as a truly empty one — saying
// "nothing selling nearby" while the buyer's own local items sit in the same list would be a lie.

/** Build the widen note, or null when there's nothing honest to say (not widened, or no distance).
 *  Distance is phrased as an UPPER BOUND — "within X km" is a ceiling, so we round the nearest metres
 *  up to whole km (min 1 km), never understating how far the buyer must reach. `immediateCount` is
 *  how many listings the un-widened radius held: 0 ⇒ the area is genuinely empty; >0 ⇒ a few are
 *  local and the farther ones are additive filler, so the copy must not claim emptiness. */
export function widenNoteText(
  widened: boolean,
  nearestDistanceM: number | null,
  immediateCount: number = 0,
): string | null {
  if (!widened || nearestDistanceM == null || nearestDistanceM <= 0) return null;
  const km = Math.max(1, Math.ceil(nearestDistanceM / 1000));
  return immediateCount > 0
    ? `Only a few sellers nearby — also showing shops within ${km} km.`
    : `Nothing selling in your immediate area — closest shops are within ${km} km.`;
}
