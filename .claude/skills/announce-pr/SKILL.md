---
name: announce-pr
description: Announce a pull request in the team's #prs Slack channel as "PR - <title>", with "PR" linking to it. Use when the user says "announce pr", "post the pr to slack", or "/announce-pr", optionally with a PR number or URL. Posts only when explicitly asked.
---

# Announce a PR in #prs

Post one line to the `#prs` Slack channel (id `C0C3UA8KGSC`):

    [PR](<pr-url>) - <pr-title>

## Steps

1. Resolve the PR. Use the number or URL the user gave; otherwise take the current branch's PR:

       gh pr view [<number-or-url>] --json number,title,url

   No PR found: say so and stop. Never guess a URL or title.

2. Send exactly this markdown with the Slack `slack_send_message` tool, `channel_id` `C0C3UA8KGSC`, `unfurl_app_links` true:

       [PR](<url>) - <title>

   Nothing else in the message: no summary, no emoji, no mentions.

3. Reply to the user with the Slack message link the tool returns.

## Rules

- Post only when the user explicitly asks to announce. Never as a side effect of opening a PR.
- One announcement per PR. If unsure whether it was already posted, read the channel first (`slack_read_channel`) and look for the URL.
- If the channel id is rejected, find `#prs` with `slack_search_channels` and use that id.
- Needs the Slack plugin: `/plugin install slack@claude-plugins-official`.
