# BAR 로컬 LLM vs Ollama Cloud

## 실행

저장소 최상단의 `Start-LLM-Duel.cmd`를 더블클릭한다. 두 모델의 응답을 먼저 확인한 후 게임을 실행한다. Ollama 앱은 실행 중이어야 하며 Cloud 사용이 가능한 계정으로 로그인되어 있어야 한다. 인증 실패나 한도 초과 시 게임을 시작하지 않고 오류 종류를 표시한다.

바탕화면의 **BAR LLM 대결** 바로가기로도 실행할 수 있다.

첫 실행은 모델과 게임 콘텐츠 로딩에 1–3분 이상 걸릴 수 있다. 실제 그래픽 엔진에서 양쪽 모델의 생산·공격 명령 적용을 확인했다. 상세 검증과 한계는 [VALIDATION.md](VALIDATION.md)를 참고한다.

- 로컬: `qwen3.5:4b`, Q4_K_M, 컨텍스트 4096, thinking 비활성화.
- 클라우드: `gpt-oss:120b-cloud`. 대형 가중치는 PC에 내려받지 않는다.
- 현재 PC: Ryzen 5 7500F, RAM 31.6GiB, RTX 4070 SUPER 12GB.
- 설정: 이 폴더의 `config.json`에서 모델, 호출 주기, 호출 횟수, 시간 제한을 변경한다.
- 클라우드 인증: 로컬 Ollama 서비스의 기존 로그인 사용. 별도 API 키를 Lua나 Git에 저장하지 않는다.

## 시연 범위

일반 BAR 전체 플레이를 자율화한 완성형 봇이 아니라, 게임을 실제로 제어하는 제한된 전략 대결 실험이다. 양쪽에 같은 실행 코드를 적용하고 모델만 다르게 연결한다.

- 맵: 설치된 Comet Catcher Remake.
- 양쪽 Armada, 커맨더·차량 공장·항공기 공장 각 1개, 경전차 6기. 항공기는 처음부터 생산 가능하다.
- 시작 자원: 금속 1500, 에너지 10000. 동일한 추가 수입: 초당 금속 15, 에너지 150.
- 모델 행동: 지상/공중 유닛 생산, 좌표로 공격, 기지 방어, 경제 투자.
- 지상: 경전차(`light`), 중형 전차(`heavy`), 정찰차(`scout`), 대공 차량(`antiair`).
- 공중: 전투기(`fighter`, 대공), 폭격기(`bomber`, 지상 폭격), 건쉽(`gunship`, 지상 공격), 정찰기(`airscout`, 비무장).
- 생산 명령은 해당 차량/항공기 공장으로 전달된다. 공장별 대기열 최대 9기이며 실제 금속·에너지를 소모한다. 공격·방어 명령에는 공중 유닛도 참여한다.
- 모델 관측에 두 공장의 생존 여부·생산 대기열·유닛별 수량을 포함한다. `unit_counts`에는 건조 중인 유닛도 포함되고, `ready_unit_counts`는 생산이 완료된 유닛만 포함한다.
- 경제 투자는 금속 300을 소비해 초당 금속 +3/에너지 +30, 최대 5회.
- 이동·사격·길찾기는 게임 엔진과 동일한 실행 규칙이 담당한다. 모델이 매 프레임 유닛을 직접 조종하는 방식이 아니다.
- 상대 유닛 정보는 해당 팀의 시야에 보이는 것만 제공한다. 상대 시작 위치는 양쪽 모두 알고 시작한다.
- 커맨더 파괴로 승리, 기본 15분 제한 후 무승부. 공장 재건·T2/T3 기술 발전·항공 수송·해군·자유 건설은 이 시연 범위 밖이다.
- 기본 모델 호출은 각 팀 20초 이상 간격, 팀당 최대 40회. 사전 연결 확인은 별도로 각 모델 1회다. 통신/출력 오류 시 다른 모델이나 스크립트로 몰래 대체하지 않고 오류를 기록한다.
- 응답이 비동기로 도착하므로 동일 규칙 비교이지, 실험 시간을 정밀하게 통제한 공식 벤치마크는 아니다. 모델 성능 비교에는 진영 교대·반복 경기·시드 통제 등이 추가로 필요하다.

## 로그와 파일

경기별 폴더는 `%LOCALAPPDATA%/BAR-LLM-Duel/날짜-식별자/`에 생성된다.

- `events.jsonl`: 관측, 선택 행동, 모델명, 응답 시간, 토큰 수, 오류, 경기 종료.
- `infolog.txt`: 게임 엔진 로그. `BAR_DUEL_READY`, `BAR_DUEL_ACTION`, `BAR_DUEL_FINISHED` 확인.
- `bridge.log`, `bridge-errors.log`: 연결 서버 진단.
- `demos/`: 엔진이 생성하는 리플레이.
- `session.json`, `startscript.txt`: 경기용 임시 통신 토큰. 클라우드 API 키가 아니다.

기존 런처 설정 대신 별도 게임 설정을 사용한다. 원본 설치된 게임/맵/엔진 데이터를 폴더 연결로 참조하므로 저장소와 `BAR-Launcher`의 상대 위치를 유지한다. 런처는 이 PC에 설치된 `recoil_2026.07.04`를 사용한다.

로컬 연결 서버는 `127.0.0.1:18765`에서만 듣는다. 게임은 해당 주소에만 연결하도록 설정한다. 실제 API 요청은 Python 연결 서버 → 로컬 Ollama → 클라우드 순서로 전송된다. 게임 관측 정보는 요청한 클라우드 모델에 전달되며 API 인증 정보는 게임에 전달되지 않는다.

## 검증 명령

```powershell
python -m unittest discover -s tools/llm_duel -p test_bridge.py
python tools/llm_duel/bridge.py --probe
./tools/llm_duel/Start-Duel.ps1 -Headless -Mock -TestSeconds 30
./tools/llm_duel/Start-Duel.ps1 -Headless -TestSeconds 90
```

`-Mock`은 연결 경로 검증 전용이며 LLM 대결이나 성능 결과로 사용하면 안 된다. 로그에 `mock: true`로 명시된다.

전체 자동 검사는 테스트용 Python 환경에 `requirements-test.txt`를 설치한 뒤 실행한다. 게임 실행 자체는 추가 Python 패키지가 필요 없다. Lua 검사는 Lupa의 Lua 5.1 런타임에서 실제 대결 Lua 코드를 실행하고 엔진 API만 모의 구현한다.

```powershell
python -m pip install -r tools/llm_duel/requirements-test.txt
python -m unittest discover -s tools/llm_duel -p "test_*.py"
python tools/llm_duel/verify_run.py "경기 로그 폴더" --require-finished
```

로그 검사는 종료 결과 누락, 대결 Lua 오류, 관측 중단, 호출·생산 제한 위반 등을 확인한다. 모델 출력 오류는 `model_errors`에 별도로 표시된다. 모의 경기 로그를 검사하려면 `--allow-mock`을 추가한다.

`-ConfigPath 파일경로`로 별도 시험 설정을 지정할 수 있다. 병렬 검사는 서로 다른 포트를 사용한다. 같은 포트로 중복 실행하면 두 번째 서버가 차단된다. `-TestSeconds`를 지정하면 경기 종료 기록을 확인한 뒤 테스트 게임이 자동 종료되며, 로딩 시간을 포함한 별도의 최대 대기 시간도 적용된다. 일반 더블클릭 실행은 경기 종료 화면을 사용자가 닫는다.

이 개발용 저장소는 실행 중 Lua 파일을 수정하면 자동 재로딩한다. 대결의 기지·공장·경제 투자·최근 명령·종료 상태를 보존하지만, 정식 비교 경기는 코드와 설정을 고정한 상태로 실행한다.

## 모델 선택 근거

Ollama 공식 Qwen3.5 4B 페이지의 Q4_K_M 모델을 사용했다. 이 PC에서 실제 로딩 후 Ollama가 보고한 GPU 모델 메모리는 약 3.13GB(10진 표기), 컨텍스트 4096이다. 게임 그래픽 메모리를 남기기 위해 이미 설치돼 있던 27B/31B 모델보다 작은 모델을 선택했다. 최초 로딩은 느릴 수 있다.

GLM-5.3 Cloud도 확인했으나 현재 계정에서 HTTP 402(추가 유료 사용 필요)가 반환됐다. 추가 결제 없이 실제 호출이 성공한 GPT-OSS 120B Cloud를 기본값으로 사용한다. 계정의 사용 한도와 제공 모델은 변경될 수 있다.

- https://ollama.com/library/qwen3.5:4b
- https://docs.ollama.com/cloud
- https://docs.ollama.com/api/chat

Codex로 작성한 실험용 코드다. 원본 프로젝트에 PR을 제출한다면 `AI_POLICY.md`에 따라 AI 사용 공개 및 사람의 검증이 필요하다.
