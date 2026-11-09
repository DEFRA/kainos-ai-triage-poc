.
├── stages/
│
│   ├── case_intake/
│   │   ├── config/                     # Capability configuration folder
│   │   │   ├── prompts/                # Capability prompts
│   │   │   └── config.yaml             # Capability configuration file containing: model used, prompt references, etc...
│   │   ├── tools/                      # Capability support tool
│   │   │   ├── email_client.py         # Connector to email server
│   │   │   ├── parse_email.py          # Parse email text
│   │   │   └── parse_attachements.py   # Parse attachements including any image attachements
│   │   ├── extract_data.py             # Extract required data from the parse files
│   │   └── intake_intelligence.py      # Capability decision module: Identify invalid/unnecessary, identify consultation type, etc...
│
│   ├── spatial_screening/
│   │   ├── config/                     # Capability configuration folder
│   │   │   └── config.yaml             # Capability configuration file containing: model used, prompt references, etc...
│   │   ├── prompts/                    # Stage-owned prompts, kept separate from model connection config
│   │   ├── tools/                      # Capability support tool
│   │   │   ├── intelligence.py         # Synchronous Ollama / Bedrock LLM client
│   │   │   ├── cwt_client.py           # Connector to the case worker tracker - likely unnecessary for PoC
│   │   │   ├── lpa_client.py           # Connector to LPA websites
│   │   │   ├── lpa_parser.py           # Wesite parser (web scraping) - likely unnecessary for PoC
│   │   │   ├── irz_client.py           # Connector to IRZ tool
│   │   │   └── irz_parser.py           # Website parser (web scraping)
│   │   ├── lpa_intelligence.py         # Prioritise LPA files and extract relevent information from the selected files: location, support documents, etc...
│   │   ├── irz_intelligence.py         # Answer IRZ questions, extract letter paragraph, other decision making 
│   │   ├── check_duplication.py        # Check for duplicate consultance on case worker tracker - likely unnecessary for PoC
│   │   └── site_history.py             # Check for historical consultations on site - likely unnecessary for PoC
│
│   ├── decision/
│   │   ├── config/                     # Capability configuration folder
│   │   │   ├── prompts/                # Capability prompts
│   │   │   └── config.yaml             # Capability configuration file containing: model used, prompt references, etc...
│   │   ├── tools/                      # Capability support tool
│   │   │   ├── query_process_kb.py     # Querying the process RAG system
│   │   │   ├── query_decision_kb.py    # Querying the decision RAG system
│   │   │   └── query_advice_kb.py      # Querying the advice RAG system
│   │   ├── implementations/            # Different implementations for decision
│   │   │   ├── jev.py
│   │   │   ├── llm.py
│   │   │   └── bert.py
│   │   └── decision_intelligence.py    # Genral decision orchestrator
│
│   ├── response/
│   │   ├── config/                     # Capability configuration folder
│   │   │   ├── prompts/                # Capability prompts
│   │   │   └── config.yaml             # Capability configuration file containing: model used, prompt references, etc...
│   │   ├── implementations/
│   │   │   ├── query_response_kb.py    # Querying the response RAG system
│   │   │   └── email_client.py         # Connector to email server
│   │   └── response_intelligence.py    # Genral response orchestrator
│
├── orchestrator/
│   ├── orchestrator.py                 # Overall system
│   └── Dockerfile
│
├── frontend/
│   ├── streamlit.py
│   └── Dockerfile
│
├── environment/                        # Environment files, general config and master deploy switch
│   ├── model_configs/                  # One YAML file per model connection
│   │   ├── ollama.yaml
│   │   └── bedrock.yaml
│   ├── .env
│   ├── config.yaml
│   └── docker-compose.yaml
│
├── data/
│
├── tests/
│
└── README.md