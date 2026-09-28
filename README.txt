TRADEAI / B2B AI TRADE NETWORK — FRONTEND PHASE 1 v0.2
======================================================

STATUS
- Frontend only.
- No API keys required.
- No backend or database required.
- No real OpenAI call.
- No real OTP / WhatsApp message.
- All data is mock/demo data in the browser.

WHAT IS INCLUDED
1. Public homepage
2. No-registration buyer enquiry UI
3. Simulated AI qualification conversation
4. Live intent score UI
5. Demo OTP flow (code 123456)
6. Top-3 supplier matching screen
7. Supplier discovery directory
8. Quote comparison screen
9. Seller landing page
10. Seller onboarding / verification preview
11. Seller dashboard
12. Opportunity detail + explainable lead score
13. Structured quote modal
14. Admin / fraud moderation preview
15. Responsive desktop/mobile styling

HOW TO VIEW ON WINDOWS — SIMPLE
1. Extract the ZIP.
2. Double-click index.html.

Recommended localhost preview:

PowerShell:
  cd "$HOME\Downloads\B2B_Frontend_Phase1_v0.2"
  py -m http.server 8080

CMD:
  cd /d "%USERPROFILE%\Downloads\B2B_Frontend_Phase1_v0.2"
  py -m http.server 8080

Then open:
  http://localhost:8080

BUYER DEMO
- On homepage, use the sample requirement or type your own.
- Continue through the questions.
- Demo OTP is 123456 (the prototype provides a one-click demo button).
- After verification, the UI shows 3 supplier matches.

STATIC VPS PREVIEW (example)
Create a preview directory under your existing nginx web root, upload this folder, and point nginx/location to index.html. No Python/FastAPI is needed for the frontend preview.

IMPORTANT
TradeAI is only a working project name. It can be replaced globally after the final brand/domain is selected.
