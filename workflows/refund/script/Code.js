/**
 * Placeholder payment records for the refund demo.
 * The request text picks one outcome. The revision owns every spoken line.
 * @param {Object} args the call arguments, with the caller's reason on request
 * @returns {Object} the outcome only
 */
function assess(args) {
  const text = String((args && args.request) || "").toLowerCase();
  const tokens = new Set(text.split(/[^a-z0-9]+/).filter(Boolean));
  if (text.includes("already refunded")) {
    return { outcome: "already_refunded" };
  }
  if (text.includes("same payment intent")) {
    return { outcome: "single_charge" };
  }
  if (tokens.has("duplicate") || tokens.has("twice") || tokens.has("double")) {
    return { outcome: "duplicate" };
  }
  return { outcome: "manual_review" };
}

/**
 * Placeholder follow-up. Swap this for a real send later.
 * It takes no address and returns no address.
 * @returns {Object} whether the follow-up was accepted
 */
function send_follow_up() {
  return { sent: true };
}

/**
 * Placeholder transfer. This demo never starts one and never calls Twilio.
 * @returns {Object} whether a teammate took the call
 */
function transfer_to_teammate() {
  return { started: false };
}

/**
 * Placeholder refund. The revision calls this only after a duplicate is confirmed.
 * @returns {Object} the result sentence
 */
function refund() {
  return { message: "The duplicate charge of twenty dollars was refunded." };
}
