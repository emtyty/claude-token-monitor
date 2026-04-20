Run the Claude Code token usage monitor and display a usage summary.

Execute these commands (the working directory is the project root where monitor.py lives):

1. Overall summary with model breakdown:
```bash
python monitor.py summary
```

2. Last 7 days daily breakdown:
```bash
python monitor.py daily --days 7
```

3. Budget status (no limits set, just shows current spend):
```bash
python monitor.py budget
```

After running, report:
- Total sessions, API calls, and all-time cost
- Which model consumed the most cost
- Today's spend and yesterday's spend
- Any notable trend from the 7-day view (e.g., spike days)
