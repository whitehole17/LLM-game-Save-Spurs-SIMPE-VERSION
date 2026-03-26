# ⚽ 토트넘 강등 저지 — 당신이 감독이다

> LLM + LangGraph + STT를 활용한 텍스트 기반 풋볼 매니저 게임

시즌 막바지, 토트넘은 강등권에 걸려 있습니다. 2026년 3월 26일 기준으로
승점 30점, 잔여 7경기. 당신이 감독이 되어 프리미어리그 잔류를 이끌어 주세요.

---

## 실행 방법

```bash
# 환경 변수 설정 후 실행
OPENAI_API_KEY=sk-... 

# .env 파일이 있는 경우
streamlit run app.py  # or 가상환경인 경우
python -m streamlit run app.py
```

**.env 파일 형식**
```
OPENAI_API_KEY=sk-...
```

---

## 파일 구조

```
simple_version/
├── app.py                  # Streamlit 앱 (최종 버전)
├── tottenham_game.ipynb    # 개발 노트북 (전체 로직 원본)
├── .env                    # API 키 (git 제외)
└── README.md
```

--- 

## 기술 스택

| 분류 | 사용 기술 |
|------|----------|
| UI | Streamlit |
| LLM | OpenAI `gpt-4o-mini` |
| STT | OpenAI `whisper-1` + SpeechRecognition |
| 상태 머신 | LangGraph `StateGraph` |
| 개발 환경 | Jupyter Notebook → `app.py` 변환 |

---

## LLM 활용 구조

이 게임은 단순 랜덤 시뮬레이션이 아니라, **4곳에서 LLM을 판단 모듈로 호출**해 게임 흐름에 개입합니다.
각각은 독립된 함수 안에서 `client.chat.completions.create()`를 직접 호출하는 방식입니다.

```
┌─────────────────────────────────────────────────────────────────┐
│                         LLM 호출 구성                             │
├──────────────────┬──────────────────────────────────────────────┤
│  함수             │  역할                                          │
├──────────────────┼──────────────────────────────────────────────┤
│ generate_halftime│ 경기 전반 45분 상황(기세/전술/멘탈)을           │
│ _situation()     │ 현장감 있는 중계 텍스트로 생성                    │
├──────────────────┼──────────────────────────────────────────────┤
│ generate_postmatch│ 경기 결과(승/무/패)를 받아                     │
│ _speech()        │ 라커룸 감독 스피치 3~5문장 생성                   │
├──────────────────┼──────────────────────────────────────────────┤
│ evaluate_press   │ 발언 스타일(신중/공격적/유머)의 상황 적절성       │
│ _conference()    │ 판단 → JSON 반환 → 컨디션 ±반영                  │
├──────────────────┼──────────────────────────────────────────────┤
│ evaluate_training│ 회복/체력/전술 비중의 균형 여부 판단             │
│ _with_llm()      │ → JSON 반환 → 컨디션 ±반영                       │
└──────────────────┴──────────────────────────────────────────────┘
```

### LLM을 판단 모듈로 활용한 방식

텍스트만 생성하는 일반적인 LLM 활용과 달리, 기자회견·훈련 평가 함수는 `response_format: json_object`로 강제해 **`condition_delta` 수치를 JSON으로 반환**받아 게임 수치에 직접 반영합니다.

```python
# LLM이 반환하는 JSON 예시
{"appropriate": false, "reason": "패배 후 유머는 선수 사기를 해칩니다", "condition_delta": -5}
{"balanced": true, "feedback": "균형 잡힌 훈련입니다", "condition_delta": +8}

# 코드에서 꺼내 바로 수치에 반영
delta = data.get("condition_delta", 0)
gs.team_condition = max(0, min(100, gs.team_condition + delta))
```

LLM이 맥락(경기 결과, 잔여 경기 수, 현재 컨디션)을 읽고 **상황에 따라 다른 delta를 출력**하기 때문에, 플레이어의 선택이 매 게임 다른 결과를 만들어냅니다.

### STT 활용

하프타임 화면에서 플레이어가 **직접 마이크로 각오를 외칠 수 있습니다**.
브라우저 녹음 → WAV 변환 → OpenAI Whisper API → 인식 텍스트 화면 출력.
게임 수치에 영향을 주지는 않지만 몰입감을 높이는 용도입니다.

---

## LangGraph 상태 머신

노트북(`tottenham_game.ipynb`) 구현 단계에서 LangGraph `StateGraph`로 전체 게임 루프를 구성했습니다.
Streamlit(`app.py`) 버전에서는 `session_state`의 `phase` 변수로 동일한 흐름을 재현합니다.

### 노드 구성

<img width="253" height="432" alt="langgraph_pic" src="https://github.com/user-attachments/assets/ea4a8197-39ab-4a26-a5bc-fe9ea2b51130" />


```
START
  │
  ▼
┌──────────┐     경기 시뮬레이션 (승률 계산 → random 결과)
│  match   │     하프타임 스피치 선택 적용
│  node    │     경기 후 LLM 스피치 생성
│          │     기자회견 LLM 평가 → 컨디션 반영
└────┬─────┘
     │
     ▼
┌──────────────────┐     경쟁팀 결과 랜덤 갱신
│ relegation_check │     check_relegation_math() 수학적 계산
│      node        │     강등/잔류/계속 여부 판정
└────┬─────────────┘
     │
     ├─── [강등 확정 or 잔류 확정 or 잔여경기 0] ──▶ ending_node ──▶ END
     │
     ▼
┌──────────┐     훈련 비중 슬라이더 입력
│ training │     LLM 훈련 평가 에이전트 → 컨디션 반영
│  node    │
└────┬─────┘
     │
     └──────────────────────────────────────────▶ match_node (다음 라운드)
```

### 조건부 엣지 (Conditional Edge)

`relegation_check_node`에서 `current_phase` 값에 따라 분기합니다.

```python
builder.add_conditional_edges(
    "relegation_check",
    lambda state: state["current_phase"],   # "training" or "ending"
    {"training": "training", "ending": "ending"},
)
```

### LangGraphState 스키마

```python
class LangGraphState(TypedDict):
    messages: Annotated[list, operator.add]  # 라운드별 로그 누적
    gs: GameState                            # 게임 전체 상태
    current_phase: str                       # "match" | "training" | "ending"
    halftime_choice: str                     # "동기부여" | "전술수정" | "질책"
    press_choice: str                        # "신중하게" | "공격적으로" | "유머로"
    training_input: dict                     # {"recovery": int, "fitness": int, "tactical": int}
```

---

## 게임 흐름 (플레이어 시점)

```
게임 시작 → 감독 이름 입력
     │
     ▼
  [하프타임 화면]
  - 다음 경기 상대 / 장소 / 예상 승률 확인
  - LLM이 생성한 전반전 상황 텍스트 (기세/전술/멘탈 중 하나)
  - 스피치 선택: 동기부여 / 전술수정 / 질책
  - (선택) 마이크로 직접 외치기 → STT 인식
     │
     ▼
  [경기 결과 화면]
  - 스코어 및 결과 (승/무/패)
  - LLM 라커룸 스피치
  - 기자회견 스타일 선택 → LLM 적절성 평가 → 컨디션 변화
     │
     ▼
  [훈련 화면]
  - 경쟁팀 결과 확인
  - 회복 / 체력 / 전술 비중 슬라이더 (합계 100%)
  - LLM 훈련 평가 → 컨디션 변화
     │
     └──▶ 다음 경기 (최대 7라운드 반복)
              │
              ▼
         [엔딩 화면]
         잔류 성공 🎉 or 강등 💔
```

---

## 수치 밸런스

### 경기 승률 계산

```
win_rate = 0.50
         + (team_condition - 50) × 0.003   # 컨디션 보정
         - (opponent_strength - 5) × 0.04  # 상대 강도 패널티
         + 0.05 (홈 경기)
→ 0.10 ~ 0.90 클리핑
```

### 하프타임 스피치 × 상황 유형 효과표

| 스피치 \ 상황 | 기세 | 전술 | 멘탈 |
|-------------|------|------|------|
| 동기부여 | 승률 +10%, 컨디션 +3 | 승률 +8%, 컨디션 +3 | **승률 +35%, 컨디션 +10** |
| 전술수정 | 승률 +8%, 컨디션 +3 | **승률 +28%, 컨디션 +10** | 승률 -10%, 컨디션 -4 |
| 질책 | **승률 +30%, 컨디션 +8** | 승률 -35%, 컨디션 -14 | 승률 -30%, 컨디션 -12 |

### 경기 결과별 컨디션 변화

| 결과 | 승점 | 컨디션 |
|------|------|--------|
| 승리 | +3 | -5 |
| 무승부 | +1 | -5 |
| 패배 | 0 | -8 |

### 경쟁팀 결과 가중치

```python
random.choices(["win", "draw", "loss"], weights=[50, 30, 20])
```

---

## 시즌 일정

| 라운드 | 상대 | 홈/어웨이 | 강도 |
|--------|------|----------|------|
| 32 | Sunderland | 어웨이 | 8/10 |
| 33 | Brighton | 홈 | 7/10 |
| 34 | Wolves | 어웨이 | 7/10 |
| 35 | Aston Villa | 어웨이 | 8/10 |
| 36 | Leeds | 홈 | 6/10 |
| 37 | Chelsea | 어웨이 | 9/10 |
| 38 | Everton | 홈 | 7/10 |

---

## 배운 점

### LLM 4개 함수를 에이전트로 만들려 했지만 포기한 이유

처음 설계할 때 전반전 상황 생성, 라커룸 스피치, 기자회견 평가, 훈련 평가를 각각 `@tool`로 정의하고 Supervisor 에이전트가 상황에 맞게 골라서 호출하는 구조를 생각했다. 하지만 실제로 구현하면서 이 게임에는 맞지 않는다는 걸 깨달았다.

핵심 이유는 **게임의 순서가 고정되어 있다**는 점이다. 에이전트 패턴은 LLM이 다음에 무엇을 해야 할지 스스로 판단해야 할 때 유용한데, 이 게임은 하프타임 → 경기 → 기자회견 → 훈련 순서가 항상 동일하다. LLM에게 판단을 맡기면 오히려 잘못된 순서로 tool을 호출하거나, 플레이어가 직접 선택해야 할 부분(하프타임 스피치, 기자회견 스타일)을 LLM이 임의로 결정해버리는 문제가 생긴다. 결국 각 함수 안에서 `client.chat.completions.create()`를 직접 호출하는 방식이 더 적합했고, LLM은 판단 모듈로만 활용했다.

### LangGraph가 좋았던 점

노트북(`tottenham_game.ipynb`)에서 전체 게임 루프를 검증할 때 LangGraph는 확실히 유용했다. `match → relegation_check → training → match` 순환 구조와 강등/잔류 확정 시 `ending`으로 빠지는 조건부 분기를 코드로 직접 짜지 않고 노드와 엣지로 선언적으로 표현할 수 있었다. 덕분에 `graph.invoke()` 한 번으로 7라운드 전체를 자동 실행하며 수치 밸런스와 엔딩 조건을 빠르게 테스트할 수 있었다.

### LangGraph가 과했던 점

노트북에서 직접 돌려보니 LangGraph의 한계도 분명했다. `graph.invoke()`는 한 번 실행하면 끝까지 달리기 때문에, 중간에 플레이어 입력(스피치 선택, 훈련 비중 설정 등)을 받는 구조와 맞지 않았다. 결국 Streamlit으로 전환할 때 LangGraph를 그대로 가져올 수 없었고, `session_state.phase` 변수로 동일한 흐름을 수동으로 재구현했다. 돌이켜보면 처음부터 Streamlit을 목표로 했다면 LangGraph 없이 상태 변수만으로 충분했을 것이다. LangGraph는 노트북 프로토타이핑 단계에서 전체 루프를 검증하는 용도로는 적절했지만, 최종 앱에서는 쓰이지 않는 과한 선택이었다.
