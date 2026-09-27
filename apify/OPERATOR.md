# Apify publication checklist

The Actor is a wrapper around `https://mcp.factrail.online/mcp`; publish it from the `apify/` directory. The backend version remains FACTRAIL 2.1.1.

1. Install the [Apify CLI](https://docs.apify.com/cli/docs/installation) if needed, then run `apify login` on the intended FACTRAIL account. From `apify/`, run `apify validate-schema` and `apify push`. The CLI creates the Actor privately if it does not already exist. Inspect its build and account before changing visibility.
2. In Console, keep **Limited permissions** and **Standby off**. The Actor needs outbound HTTPS plus its own default dataset/key-value store and the PPE charging API. Do not mount backend credentials or request Full permissions.
3. Configure **Pay Per Event**, with **Pay Per Event + platform usage off**. Define the two custom events and prices in `.actor/pricing-plan.json`, mark `factrail-verify` primary, and disable the synthetic `apify-actor-start` and `apify-default-dataset-item` events. Their defaults would violate the no-charge-on-failure promise.
4. Verify the exact accepted event prices and one-time-per-run settings in Console. If $0.005 or $0.03 is rejected, pause before public publication and record the allowed constraint and proposed closest price.
5. Test one supported verify, one deliberately partial import, one malformed receipt, and one valid receipt. Confirm event logs show one verify charge and zero charges for the other three. Confirm budget-limited runs return `budget_exceeded` without a charged result.
6. Confirm Store title, description, categories, README, output, and scope; confirm no secrets in the build. Publish publicly only after PPE behavior is verified.
7. Check developer identity verification (KYC) in Apify Console. Do not claim x402/Skyfire eligibility until Apify reports `allowsAgenticUsers=true` for the published Actor.

No account credentials, KYC state, accepted prices, or Actor listing are stored in this repository. `pricing-plan.json` is operator metadata, not an automatically applied Apify pricing API configuration.
