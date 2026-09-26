# UI Guidance System

The MVP now contains a reusable bilingual guidance layer.

## Component

`.ui-explainer`

Supported semantic variants:

- `.ui-explainer--ai`
- `.ui-explainer--info`
- `.ui-explainer--success`
- `.ui-explainer--orange`

Each explainer can contain:

- title
- French description
- Algerian Darija description
- optional example
- contextual icon

## Behaviour

- Navigation: hover/focus on desktop; tap on mobile.
- Panel controls: compact inline helper cards.
- Furniture cards: `i` indicator opens a contextual product explanation.
- AI Assistant: dedicated introductory helper card.
- First visit: short onboarding with skip and remember controls.

All guidance is frontend-only and does not alter the backend or AI pipeline.
