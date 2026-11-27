# GitDoc Hub

A hosted web workspace inside the GitDoc source repository. The Hub has its own repositories and storage. A repository owner can invite collaborators with an email-bound link and grant read or edit access. Each invitee also needs access to the deployed Site.

Markdown and plaintext are editable in the browser. Yjs merges simultaneous edits to a single document. The browser checks for updates every 900 ms and preserves local edits while retrying after connection failures. Checkpoints record named versions independently of live edits. A document proposal merges one shared document from another branch. Branches, issues, comments, releases, stars, member permissions, and activity are available in the web workspace or through its Site MCP plugin.

## Run the Hub

```sh
npm ci
npm run build
npm test
npm run dev
```

Open [the local Hub](http://127.0.0.1:4176/). The loopback test server uses a fixture identity and an ignored SQLite database. Hosted requests use ChatGPT Sites authentication and D1. The first production deployment uses owner-only Site access. To share it, grant the invited account Site access and create a matching repository invitation. Invitation links expire after seven days.

The Hub currently stores collaborative text documents. The desktop application manages HWP and Office originals in its local store. GitDoc Hub repositories and branches are application records. The project's hub source lives in the GitHub repository.
