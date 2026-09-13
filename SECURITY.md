# Security policy

Report vulnerabilities privately to **kakon77777@evemisslab.com** (EVEMISS TECHNOLOGY CO., LTD.).
Please do not open a public issue for a security problem. You will get an acknowledgement within
7 days and a fix or a decision within 30 days for confirmed reports.

Scope notes for this lab:

- Repository text analysed by the pipeline is treated as untrusted data; packets are scrubbed for local
  paths and credentials before any model sees them, and every worker contract states the data boundary.
- The pipeline never holds a provider credential. Model calls go through MACR, which keeps its own
  key file inside its bounded runtime.
- If you find a way to make a worker follow instructions embedded in repository content, or to leak
  data from a packet into a published asset, that is in scope.
