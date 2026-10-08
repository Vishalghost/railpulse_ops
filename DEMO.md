# RailPulse Ops — 5-minute demo

A run sheet for presenting RailPulse live. Each beat says what to show, what to say, and what
to do if it doesn't cooperate.

## Before you present (T–20 min)

1. **Deploy demo settings** (shortened timers so things happen within the talk):
   ```powershell
   cd infra
   cdk deploy --all -c ops_email=you@example.com -c alert_email=you@example.com `
     -c sms_mode=dry_run -c ack_timeout_override_s=60 -c silent_after_s=120
   ```
2. **Seed and sign in:** `python simulator\seed_assets.py`, then
   `python tools\create_user.py --username demo --email you@example.com --role Admin`.
   Sign in once and set the password, so the login screen doesn't appear on stage.
3. **Start the pre-roll at exactly T–5 min**, with the same silence threshold as the deploy:
   `.\scripts\demo.ps1 -SilentAfter 120`. It prints the timeline (times from script start, so
   subtract 5:00 for talk time). Keep that window visible to you only. The Chennai segment goes
   dark at T–4:00, so its P2 lands somewhere between T–2 and about T+3 (next 5-minute heartbeat).
4. **Open tabs in this order:** Dashboard (Delhi) · Dashboard (Chennai) · CloudWatch dashboard
   `railpulse-dev` · Step Functions `railpulse-dev-escalation` · Athena (workgroup
   `railpulse-dev`) · your email/phone.

## The five minutes

| Time | Show | Say | If it misbehaves |
|---|---|---|---|
| 0:00 | Dashboard, Delhi: the track strip | "Every lamp is a kilometre of track. Green means its vibration, temperature and ultrasonic sensors all look healthy. 210 sensors are streaming over MQTT into IoT Core right now." | Feed says *reconnecting*: reload. The strip loads on its own without the live feed. |
| 0:40 | Incident appears (crack beat) and the lamp flashes red | "A crack. The ultrasonic echo dropped 6 dB below this sensor's own baseline, so it opened as P2. As the crack grew it went to P1, and it's still one incident, not dozens." | No crack (`apply_fault()` not written yet): skip straight to the silent segment at 2:45. |
| 1:30 | Incident page: echo chart with the limit line, the escalation ladder | "The gangman on this beat was paged in Hindi, with a voice clip. If he doesn't acknowledge within the timeout, Step Functions pages the Section Engineer, then the Divisional Engineer." | No SMS: show the email copy. It's the same text. |
| 2:00 | Phone/email: open the ack link, then tap **confirm** | "Opening the link changes nothing, because SMS apps open links for previews. Only the button acknowledges." The incident's chip turns to *Acknowledged · gm-dli-2* and it stops flashing in the list. The lamp stays red: lamps show severity, not status. | Ack page 403: it was opened via the raw Lambda URL. Use the link from the email. |
| 2:45 | Dashboard, Chennai: "1 gone silent", P2 *Segment gone silent* | "Nobody sends an alarm when a cable is stolen. Silence itself is the signal. A heartbeat turns missing data into an incident." | Not there yet: it appears at the next 5-minute heartbeat. Come back to it at the end. |
| 3:15 | CloudWatch dashboard during the burst | "Peak transit: five times the traffic. The queue absorbs it, the worker fleet scales out on backlog, and the backlog drains. The web tier scales separately on CPU over 70%." | Instance count unchanged: point at the backlog graph. Scaling takes about 2 minutes to show. |
| 4:00 | Athena: saved query *Incidents by type and hour of day* | "Every event also lands in an S3 data lake. Months of track history are one SQL query away, capped at 1 GB scanned per query." | Empty result: Firehose buffers for up to 5 minutes. Run *Sensors that under-reported* instead. |
| 4:30 | Back to the dashboard | "WAF and CloudFront in front, KMS on every table, IMDSv2-only instances, and the whole platform redeploys from one `cdk deploy`." | — |

## After

```powershell
cd simulator; python provision.py --teardown
cd ..\infra;  cdk destroy --all
```
Don't leave it running between demo days: the always-on parts (two small instances, a load
balancer and WAF) add up even with the simulator stopped. A $50 Budgets alert emails you at
80% of spend, but it only warns; it doesn't stop anything.
