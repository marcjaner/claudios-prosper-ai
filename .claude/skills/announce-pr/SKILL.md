---
name: announce-pr
description: Announce a pull request in the team's #prs Slack channel as "PR - <title>", with "PR" linking to it. Use when the user says "announce pr", "post the pr to slack", or "/announce-pr", optionally with a PR number or URL. Posts only when explicitly asked.
---

# Announce a PR in #prs

Post one line to the `#prs` Slack channel:

    [PR](<pr-url>) - <pr-title>

## Steps

1. Resolve the PR. Use the number or URL the user gave; otherwise take the current branch's PR:

       gh pr view [<number-or-url>] --json number,title,url

   No PR found: say so and stop. Never guess a URL or title.

2. Read the channel id from `.env`: `SLACK_PRS_CHANNEL_ID` (documented in `.env.example`). If it is missing, find `#prs` with `slack_search_channels`.

3. Send exactly this markdown with the Slack `slack_send_message` tool, that `channel_id`, `unfurl_app_links` true:

       [PR](<url>) - <title>

   Nothing else in the message: no summary, no emoji, no mentions.

4. Reply to the user with the Slack message link the tool returns.

## Rules

- Post only when the user explicitly asks to announce. Never as a side effect of opening a PR.
- One announcement per PR. If unsure whether it was already posted, read the channel first (`slack_read_channel`) and look for the URL.
- Needs the Slack plugin: `/plugin install slack@claude-plugins-official`.
