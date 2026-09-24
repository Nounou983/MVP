# Cloud smoke test — Phase 8

After deploying the ZeroGPU Space:

1. Open the Space and verify it reaches `Running`.
2. Run Analyze on a room photo.
3. Run object selection on a furniture point.
4. Run Remove with AI.
5. Confirm the returned image and quality metadata.
6. Record the first-model load time and inference duration.
7. Configure the production `deployment-config.js` with the deployed API/gateway endpoint.
8. Test one end-to-end client flow from a clean browser session.

Do not expose model credentials in the frontend.
