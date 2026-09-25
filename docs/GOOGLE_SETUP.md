# Connecting E.V.A. to Google Calendar and Gmail

One Google sign-in covers both. About 15 minutes, once.

## 1. Create a Google Cloud project
1. Go to https://console.cloud.google.com and sign in with the Google account whose calendar and mail you want.
2. Top bar: project picker, then **New project**. Name it `EVA`. Create, then select it.

## 2. Turn on the two APIs
1. **APIs & Services > Library**.
2. Search **Google Calendar API**, open it, **Enable**.
3. Search **Gmail API**, open it, **Enable**.

## 3. Consent screen
1. **APIs & Services > OAuth consent screen** (may be called **Google Auth Platform > Branding / Audience**).
2. User type **External**. App name `EVA`, your email as support and developer contact. Save.
3. Under **Audience** (or **Test users**), add your own Gmail address as a test user.
4. Leave the app in **Testing**. That's correct for personal use.

## 4. Create the OAuth client
1. **APIs & Services > Credentials > Create credentials > OAuth client ID**.
2. Application type **Desktop app**, name `EVA desktop`. Create.
3. **Download JSON**. Save it as:
   `D:\Project E.V.A\secrets\gcp-oauth.keys.json`
   That folder is next to the repo, not inside it, so it can never be pushed to GitHub.

## 5. Sign in
In PowerShell, from `D:\Project E.V.A\eva` with the venv active:

    python -m core.google_api

A browser opens. Choose your account. Google will say the app isn't verified: click **Advanced > Go to EVA (unsafe)**.
That warning appears because you are the developer of your own private app. Approve the permissions.

Or skip the terminal: just ask E.V.A. "what's on my calendar?" and she opens the same page herself.

## What she can and can't do
- Calendar: read events, add events, delete events. Adding and deleting always ask you first.
- Gmail: read and search mail, create **drafts**. She has no permission to send. Every draft waits in Gmail for you.
- The token lives in `data/google/token.json` (git-ignored) and refreshes itself.

## Good to know
- In **Testing** mode Google expires the sign-in after 7 days. When it lapses she tells you and opens the
  sign-in page again; approving takes ten seconds.
- To disconnect: delete `data/google/token.json`, and remove EVA at https://myaccount.google.com/permissions.
