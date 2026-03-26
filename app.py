# app.py — 토트넘 강등 저지 게임 (Streamlit 버전)

import os
import json
import random
import tempfile
from dataclasses import dataclass, field

import speech_recognition as sr
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ─────────────────────────────────────────
# GameState
# ─────────────────────────────────────────
@dataclass
class GameState:
    points: int = 30
    remain_round: int = 7
    position: int = 16
    team_condition: int = 40      # 0~100

    game_over: bool = False
    ending_type: str = ""         # "survival" | "relegation"
    match_history: list = field(default_factory=list)

    schedule: list = field(default_factory=lambda: [
        {"round": 32, "opponent": "Sunderland",  "is_home": False, "strength": 8},
        {"round": 33, "opponent": "Brighton",    "is_home": True,  "strength": 7},
        {"round": 34, "opponent": "Wolves",      "is_home": False, "strength": 7},
        {"round": 35, "opponent": "Aston Villa", "is_home": False, "strength": 8},
        {"round": 36, "opponent": "Leeds",       "is_home": True,  "strength": 6},
        {"round": 37, "opponent": "Chelsea",     "is_home": False, "strength": 9},
        {"round": 38, "opponent": "Everton",     "is_home": True,  "strength": 7},
    ])

    rival_teams: dict = field(default_factory=lambda: {
        "Nottingham Forest": {"points": 32, "remaining": 7},
        "West Ham":          {"points": 29, "remaining": 7},
        "Leeds United":      {"points": 33, "remaining": 7},
        "Burnley":           {"points": 19, "remaining": 7},
        "Wolves":            {"points": 17, "remaining": 7},
    })


# ─────────────────────────────────────────
# 게임 로직 함수
# ─────────────────────────────────────────

def check_relegation_math(gs: GameState) -> dict:
    # 경쟁팀 최대 가능 승점 내림차순
    max_pts = sorted(
        [v["points"] + v["remaining"] * 3 for v in gs.rival_teams.values()],
        reverse=True
    )
    safe_line = max_pts[2]  # 18위 예정 팀 기준 (5팀 중 index 2)
    is_safe = gs.points > safe_line
    is_relegated = (gs.points + gs.remain_round * 3) < max_pts[-1]
    return {"is_safe": is_safe, "is_relegated": is_relegated, "safe_line": safe_line}


def calculate_win_rate(gs: GameState, match: dict) -> float:
    base = 0.50
    condition_bonus  = (gs.team_condition - 50) * 0.003
    strength_penalty = (match["strength"] - 5) * 0.04
    home_bonus       = 0.05 if match["is_home"] else 0.0
    rate = base + condition_bonus + home_bonus - strength_penalty
    return max(0.10, min(0.90, rate))


def simulate_match(gs: GameState, halftime_bonus: float = 0.0) -> dict:
    match    = gs.schedule[0]
    # halftime_bonus: 스피치 선택 효과 반영 (±0.30~0.38)
    win_rate = max(0.10, min(0.90, calculate_win_rate(gs, match) + halftime_bonus))
    draw_rate = (1 - win_rate) * 0.3
    loss_rate = (1 - win_rate) * 0.7
    result = random.choices(["win", "draw", "loss"], weights=[win_rate, draw_rate, loss_rate])[0]

    scores = {"win":  ["1-0", "2-0", "2-1", "3-0", "3-1"],
              "draw": ["0-0", "1-1", "2-2"],
              "loss": ["0-1", "0-2", "1-2"]}
    score = random.choice(scores[result])

    pts_delta  = {"win": 3, "draw": 1, "loss": 0}
    cond_delta = {"win": -5, "draw": -5, "loss": -8}
    gs.points         += pts_delta[result]
    gs.team_condition  = max(0, min(100, gs.team_condition + cond_delta[result]))
    gs.remain_round   -= 1
    gs.schedule.pop(0)

    summary = {"result": result, "opponent": match["opponent"], "score": score,
               "round": match["round"], "win_rate": round(win_rate, 2)}
    gs.match_history.append(summary)
    return summary


def update_rival_results(gs: GameState) -> dict:
    summary = {}
    for team, data in gs.rival_teams.items():
        if data["remaining"] <= 0:
            continue
        result = random.choices(["win", "draw", "loss"], weights=[50, 30, 20])[0]
        pts = {"win": 3, "draw": 1, "loss": 0}[result]
        data["points"]    += pts
        data["remaining"] -= 1
        summary[team] = {"result": result, "pts_gained": pts, "total": data["points"]}
    return summary


HALFTIME_SPEECHES = {
    "동기부여": "우리는 지금 최선을 다하고 있다. 후반전, 한 골만 더 내면 된다. 포기하지 마라!",
    "전술수정": "수비 라인을 올려라. 역습 시 왼쪽 공간을 활용해라. 집중하자.",
    "질책":     "지금 뭐하는 거냐. 이게 최선이냐. 후반전엔 제대로 뛰어라.",
}

def get_halftime_speech(choice: str) -> str:
    return HALFTIME_SPEECHES[choice]


# 하프타임 스피치 × 전반전 상황 효과표
# (스피치, 상황유형): (후반전 승률 보정, 컨디션 변화)
# 기세 상황: 질책이 최적 (충격 요법), 나머지는 중립
# 전술 상황: 전술수정이 최적, 질책은 역효과
# 멘탈 상황: 동기부여가 최적, 질책은 최악
HALFTIME_EFFECTS = {
    ("동기부여", "기세"):  (+0.10, +3),
    ("동기부여", "전술"):  (+0.08, +3),
    ("동기부여", "멘탈"):  (+0.35, +10),
    ("전술수정", "기세"):  (+0.08, +3),
    ("전술수정", "전술"):  (+0.38, +12),
    ("전술수정", "멘탈"):  (-0.10, -4),
    ("질책",    "기세"):  (+0.30, +8),
    ("질책",    "전술"):  (-0.20, -8),
    ("질책",    "멘탈"):  (-0.35, -12),
}


# 전반전 상황 유형: (레이블, LLM에 줄 배경 설명)
_SITUATION_TYPES = [
    ("기세",   "상대팀에게 기세에서 완전히 밀리고 있다. 공 점유율이 처참하고 선수들이 압도당하고 있다."),
    ("전술",   "상대 감독의 전술에 완전히 무너지고 있다. 우리 수비 라인이 뻔히 뚫리고 있다."),
    ("멘탈",   "선수들의 집중력이 흐트러져 혼이 빠진 상태다. 실책이 연발되고 패닉 상태다."),
]

def generate_halftime_situation(gs: GameState, match: dict) -> tuple[str, str]:
    """전반전 45분 상황을 LLM이 3가지 유형 중 하나로 생성.
    Returns: (sit_label, display_text)  — sit_label은 효과 계산에 사용."""
    sit_label, sit_context = random.choice(_SITUATION_TYPES)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=1.3,
        messages=[
            {"role": "system", "content": (
                "토트넘 경기 생중계 해설자다. "
                "전반전 45분 상황을 현장감 있게 2~3문장으로 중계한다. "
                "한국어. 과장 없이 현실적으로."
            )},
            {"role": "user", "content": (
                f"상황 유형: {sit_label}으로 밀리는 중. {sit_context} "
                f"상대: {match['opponent']} (강도 {match['strength']}/10). "
                f"장소: {'홈' if match['is_home'] else '어웨이'}. "
                f"토트넘 컨디션: {gs.team_condition}/100."
            )},
        ],
    )
    text = f"**[{sit_label}으로 밀리는 중]**\n\n{response.choices[0].message.content}"
    return sit_label, text


def transcribe_speech(timeout: int = 15) -> str:
    """마이크 입력 STT — 노트북 전용. Streamlit에서는 transcribe_from_file() 사용."""
    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=10)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio.get_wav_data())
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(file=f, model="whisper-1")
        os.unlink(tmp_path)
        return result.text
    except sr.WaitTimeoutError:
        return ""
    except Exception:
        return ""


def transcribe_from_file(audio_bytes: bytes, suffix: str = ".wav") -> str:
    """업로드된 오디오 파일 STT — Streamlit 전용."""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(file=f, model="whisper-1")
        os.unlink(tmp_path)
        return result.text
    except Exception:
        return ""


def generate_postmatch_speech(gs: GameState, result: str, manager_name: str = "") -> str:
    opponent  = gs.match_history[-1]["opponent"] if gs.match_history else "상대팀"
    name_part = f"감독 이름은 {manager_name}이다. 스피치 중 한 번 '{manager_name} 감독'이라고 자신을 언급해도 좋다." if manager_name else ""
    response  = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"토트넘 감독으로서 경기 직후 라커룸에서 선수단에게 짧고 현실적인 스피치를 한다. 한국어로 3~5문장. {name_part}"},
            {"role": "user",   "content": f"방금 {opponent}와 {result} 했다. 현재 승점 {gs.points}, 잔여 {gs.remain_round}경기, 컨디션 {gs.team_condition}/100."}
        ]
    )
    return response.choices[0].message.content


def evaluate_press_conference(gs: GameState, choice: str, match_result: str) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": '축구 감독의 기자회견 발언이 현재 상황에 적절한지 평가한다. JSON으로만 반환: {"appropriate": true/false, "reason": "str", "condition_delta": int}. condition_delta 범위: -8~+5.'},
            {"role": "user",   "content": f"발언 스타일: {choice}. 경기 결과: {match_result}. 잔여경기: {gs.remain_round}. 컨디션: {gs.team_condition}."}
        ]
    )
    data  = json.loads(response.choices[0].message.content)
    delta = data.get("condition_delta", 0)
    gs.team_condition = max(0, min(100, gs.team_condition + delta))
    return {"appropriate": data.get("appropriate", True), "reason": data.get("reason", ""), "condition_delta": delta}


def evaluate_training_with_llm(gs: GameState, recovery: int, fitness: int, tactical: int) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": '축구팀 훈련 코치다. 훈련 비중이 적절한지 평가한다. JSON으로만 반환: {"balanced": true/false, "feedback": "str", "condition_delta": int}. condition_delta 범위: -10~+10.'},
            {"role": "user",   "content": f"훈련 비중 — 회복:{recovery}% 체력:{fitness}% 전술:{tactical}%. 현재 컨디션:{gs.team_condition}, 잔여경기:{gs.remain_round}."}
        ]
    )
    data  = json.loads(response.choices[0].message.content)
    delta = data.get("condition_delta", 0)
    gs.team_condition = max(0, min(100, gs.team_condition + delta))
    return {"balanced": data.get("balanced", True), "feedback": data.get("feedback", ""), "condition_delta": delta}


# ─────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────
st.set_page_config(page_title="토트넘 강등 저지", layout="wide", page_icon="⚽")


def render_sidebar():
    gs   = st.session_state.gs
    name = st.session_state.get("manager_name", "")
    with st.sidebar:
        if name:
            st.markdown(f"### 👔 {name} 감독님")
        st.header("⚽ 팀 현황")

        col1, col2 = st.columns(2)
        col1.metric("승점", f"{gs.points}pt")
        col2.metric("잔여 경기", f"{gs.remain_round}경기")
        st.metric("현재 순위", f"{gs.position}위")

        # 컨디션 게이지
        cond = gs.team_condition
        cond_icon = "🟢" if cond >= 70 else "🟡" if cond >= 50 else "🔴"
        st.write(f"**컨디션** {cond_icon}  {cond}/100")
        st.progress(cond / 100)

        st.divider()
        st.subheader("경쟁팀 현황")
        for team, data in gs.rival_teams.items():
            max_possible = data["points"] + data["remaining"] * 3
            st.write(f"**{team}**  {data['points']}pt  *(최대 {max_possible}pt)*")


# ── 시작 화면 ──────────────────────────────
def render_start():
    st.title("⚽ 토트넘 강등 저지")
    st.subheader("당신이 감독이다")
    st.divider()

    col1, col2, col3 = st.columns(3)
    col1.metric("현재 승점", "37pt")
    col2.metric("잔여 경기", "7경기")
    col3.metric("현재 순위", "16위")

    st.info(
        "시즌 막바지, 토트넘은 강등권에 걸려 있습니다. "
        "남은 7경기를 버텨내고 프리미어리그 잔류를 이끌어 주세요."
    )

    name_input = st.text_input(
        "감독님 성함을 입력해주세요",
        placeholder="예: 홍길동",
        max_chars=20,
        key="name_input_field",
    )

    if st.button(
        "🏟️ 게임 시작",
        type="primary",
        use_container_width=True,
        disabled=(name_input.strip() == ""),
    ):
        # session_state 초기화
        st.session_state.manager_name            = name_input.strip()
        st.session_state.gs                      = GameState()
        st.session_state.phase                   = "halftime"
        st.session_state.last_match              = None
        st.session_state.postmatch_speech        = ""
        st.session_state.press_result            = None
        st.session_state.train_result            = None
        st.session_state.relegation_result       = None
        st.session_state.halftime_situation      = None
        st.session_state.halftime_situation_type = None
        st.rerun()


# ── 하프타임 화면 ──────────────────────────
def render_halftime():
    gs = st.session_state.gs

    next_match = gs.schedule[0]
    win_rate   = calculate_win_rate(gs, next_match)
    home_away  = "홈" if next_match["is_home"] else "어웨이"

    st.header(f"🏟️ 라운드 {next_match['round']} — vs {next_match['opponent']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("장소", home_away)
    col2.metric("상대 강도", f"{next_match['strength']}/10")
    col3.metric("예상 승률", f"{win_rate * 100:.0f}%")

    name = st.session_state.get("manager_name", "")
    st.divider()
    st.subheader(f"⏸️ 전반전 진행 중...  {'— ' + name + ' 감독님, 지시를 내려주세요!' if name else ''}")

    # 전반전 상황: 처음 진입 시 LLM 생성 후 session_state에 저장 (rerun 시 재생성 방지)
    if st.session_state.get("halftime_situation") is None:
        with st.spinner("전반전 상황 분석 중..."):
            sit_label, sit_text = generate_halftime_situation(gs, next_match)
            st.session_state.halftime_situation      = sit_text
            st.session_state.halftime_situation_type = sit_label
    st.warning(st.session_state.halftime_situation)

    st.divider()

    # 하프타임 스피치 선택 → 텍스트 + 효과 미리보기 표시
    choice = st.radio(
        "상황을 보고 하프타임 스피치를 선택하세요:",
        options=["동기부여", "전술수정", "질책"],
        horizontal=True,
        key="halftime_choice",
    )
    st.info(f'"{get_halftime_speech(choice)}"')

    # 현재 선택 × 상황 유형 효과 미리보기
    sit_type = st.session_state.get("halftime_situation_type", "기세")
    wr_delta, cond_delta = HALFTIME_EFFECTS.get((choice, sit_type), (0, 0))
    if wr_delta >= 0.25:
        effect_label = "✅ 현재 상황에 최적"
    elif wr_delta >= 0.05:
        effect_label = "🔶 어느 정도 효과"
    else:
        effect_label = "❌ 현재 상황에 부적절"
    wr_str   = f"+{wr_delta*100:.0f}%" if wr_delta >= 0 else f"{wr_delta*100:.0f}%"
    cond_str = f"+{cond_delta}" if cond_delta >= 0 else str(cond_delta)
    st.caption(f"{effect_label}  |  후반전 승률 {wr_str}  /  컨디션 {cond_str}")

    st.divider()

    # STT: 브라우저 녹음 버튼 (st.audio_input, Streamlit 1.38+)
    st.subheader("🎙️ 직접 외쳐보기 (선택)")
    audio_value = st.audio_input(
        "녹음 버튼을 누르고 각오를 외쳐보세요!",
        key="halftime_audio",
    )
    if audio_value is not None:
        with st.spinner("음성 인식 중..."):
            stt_text = transcribe_from_file(audio_value.read(), suffix=".wav")
        if stt_text:
            st.success(f"🎤 인식 결과: {stt_text}")
        else:
            st.warning("음성 인식에 실패했습니다. 위 텍스트 스피치를 사용합니다.")

    st.divider()

    if st.button("▶️ 후반전 시작!", type="primary", use_container_width=True):
        # 스피치 효과 적용: 컨디션 즉시 반영, 승률 보정값은 simulate_match에 전달
        sit_type            = st.session_state.get("halftime_situation_type", "기세")
        wr_bonus, c_delta   = HALFTIME_EFFECTS.get((choice, sit_type), (0, 0))
        gs.team_condition   = max(0, min(100, gs.team_condition + c_delta))

        with st.spinner("경기 진행 중..."):
            match_summary = simulate_match(gs, halftime_bonus=wr_bonus)
            speech        = generate_postmatch_speech(
                gs, match_summary["result"],
                manager_name=st.session_state.get("manager_name", ""),
            )
        st.session_state.last_match              = match_summary
        st.session_state.postmatch_speech        = speech
        st.session_state.halftime_situation      = None  # 다음 경기 진입 시 새로 생성
        st.session_state.halftime_situation_type = None
        st.session_state.phase                   = "match_result"
        st.rerun()


# ── 경기 결과 화면 ─────────────────────────
def render_match_result():
    gs      = st.session_state.gs
    summary = st.session_state.last_match

    result_map = {"win": ("승리 🎉", "🎉"), "draw": ("무승부 🤝", "🤝"), "loss": ("패배 😞", "😞")}
    result_label, _ = result_map[summary["result"]]

    st.header(f"vs {summary['opponent']} — {result_label}")

    col1, col2, col3 = st.columns(3)
    col1.metric("스코어", summary["score"])
    col2.metric("누적 승점", f"{gs.points}pt")
    col3.metric("컨디션", f"{gs.team_condition}/100")

    # 경기 후 라커룸 스피치
    st.divider()
    st.subheader("🗣️ 라커룸 스피치")
    st.write(st.session_state.postmatch_speech)

    # 기자회견 선택
    st.divider()
    st.subheader("📰 기자회견")
    press_choice = st.radio(
        "발언 스타일 선택:",
        options=["신중하게", "공격적으로", "유머로"],
        horizontal=True,
        key="press_choice",
    )

    if st.button("➡️ 다음으로", type="primary", use_container_width=True):
        with st.spinner("기자회견 평가 및 강등 계산 중..."):
            press_result = evaluate_press_conference(gs, press_choice, summary["result"])
            rivals       = update_rival_results(gs)
            rel_result   = check_relegation_math(gs)

        # 강등 조건 판정
        if rel_result["is_relegated"]:
            gs.game_over   = True
            gs.ending_type = "relegation"
        elif rel_result["is_safe"] or gs.remain_round == 0:
            gs.game_over   = True
            gs.ending_type = "survival" if gs.points > rel_result["safe_line"] else "relegation"

        st.session_state.press_result      = press_result
        st.session_state.relegation_result = rel_result
        st.session_state.train_result      = None  # 새 훈련 phase 진입 시 초기화

        if gs.game_over:
            st.session_state.phase = "ending"
        else:
            st.session_state.phase = "training"
        st.rerun()


# ── 훈련 화면 ──────────────────────────────
def render_training():
    gs = st.session_state.gs

    # 직전 경기 결과 배너
    rel = st.session_state.relegation_result
    if rel:
        if rel["is_safe"]:
            st.success(f"✅ 수학적 잔류 확정! (안전선 {rel['safe_line']}pt 초과)")
        else:
            st.warning(f"⚠️ 아직 안심할 수 없습니다. 현재 안전선: {rel['safe_line']}pt")

    pr = st.session_state.press_result
    if pr:
        icon      = "✅" if pr["appropriate"] else "❌"
        delta_str = f"+{pr['condition_delta']}" if pr["condition_delta"] >= 0 else str(pr["condition_delta"])
        st.info(f"{icon} 기자회견: {pr['reason']}  (컨디션 {delta_str})")

    st.divider()
    st.header("🏃 훈련 설정")

    # 다음 경기 미리보기
    next_match = gs.schedule[0]
    win_rate   = calculate_win_rate(gs, next_match)
    home_away  = "홈" if next_match["is_home"] else "어웨이"

    col1, col2, col3 = st.columns(3)
    col1.metric("다음 상대", next_match["opponent"])
    col2.metric("장소", home_away)
    col3.metric("예상 승률", f"{win_rate * 100:.0f}%")

    st.divider()
    st.subheader("훈련 비중 설정 (합계 100%)")

    recovery = st.slider("💤 회복 훈련 %", 0, 100, 34, key="tr_recovery")
    fitness  = st.slider("💪 체력 훈련 %", 0, 100, 33, key="tr_fitness")
    tactical = st.slider("🧠 전술 훈련 %", 0, 100, 33, key="tr_tactical")

    total = recovery + fitness + tactical
    if total != 100:
        st.warning(f"⚠️ 합계 {total}% — 정확히 100%로 맞춰주세요.")
    else:
        st.success(f"합계: {total}% ✅")

    # 훈련 실행 결과 표시
    if st.session_state.train_result:
        tr        = st.session_state.train_result
        icon      = "✅" if tr["balanced"] else "⚠️"
        delta_str = f"+{tr['condition_delta']}" if tr["condition_delta"] >= 0 else str(tr["condition_delta"])
        st.info(f"{icon} {tr['feedback']}  (컨디션 {delta_str} → {gs.team_condition}/100)")

    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        if st.button("🏃 훈련 실행", disabled=(total != 100), use_container_width=True):
            with st.spinner("훈련 평가 중..."):
                train_result = evaluate_training_with_llm(gs, recovery, fitness, tactical)
            st.session_state.train_result = train_result
            st.rerun()

    with col_b:
        # 훈련을 먼저 실행해야 다음 경기로 이동 가능
        if st.button(
            "➡️ 다음 경기로",
            disabled=(st.session_state.train_result is None),
            use_container_width=True,
            type="primary",
        ):
            st.session_state.halftime_situation      = None  # 새 경기 전반전 상황 초기화
            st.session_state.halftime_situation_type = None
            st.session_state.phase = "halftime"
            st.rerun()


# ── 엔딩 화면 ──────────────────────────────
def render_ending():
    gs   = st.session_state.gs
    name = st.session_state.get("manager_name", "")

    if gs.ending_type == "survival":
        st.balloons()
        # 감독 이름 크게 출력
        if name:
            st.markdown(f"<h1 style='text-align:center; font-size:3rem;'>🏆 {name} 감독님!</h1>", unsafe_allow_html=True)
        st.title("🎉 잔류 성공!")
        st.subheader("토트넘은 프리미어리그에 남았습니다!")
        col1, col2 = st.columns(2)
        col1.metric("최종 승점", f"{gs.points}pt")
        col2.metric("플레이 경기 수", f"{7 - gs.remain_round}경기")
    else:
        if name:
            st.markdown(f"<h1 style='text-align:center; font-size:3rem; color:#888;'>😔 {name} 감독님...</h1>", unsafe_allow_html=True)
        st.title("💔 강등 확정")
        st.subheader("토트넘은 챔피언십으로 강등되었습니다...")
        col1, col2 = st.columns(2)
        col1.metric("최종 승점", f"{gs.points}pt")
        col2.metric("잔여 경기", f"{gs.remain_round}경기")

    # 경기 기록
    st.divider()
    st.subheader("📋 경기 기록")
    icons = {"win": "🎉", "draw": "🤝", "loss": "😞"}
    for h in gs.match_history:
        st.write(
            f"{icons.get(h['result'], '')} R{h['round']}  vs **{h['opponent']}**  "
            f"**{h['score']}**  (예상 승률 {h['win_rate'] * 100:.0f}%)"
        )

    st.divider()
    if st.button("🔄 다시 시작", use_container_width=True):
        # session_state 전체 초기화
        keys_to_clear = [
            "gs", "phase", "last_match", "postmatch_speech",
            "press_result", "train_result", "relegation_result",
            "halftime_situation", "halftime_situation_type",
            "manager_name", "name_input_field",
            "halftime_choice", "press_choice", "halftime_audio",
            "tr_recovery", "tr_fitness", "tr_tactical",
        ]
        for k in keys_to_clear:
            st.session_state.pop(k, None)
        st.rerun()


# ─────────────────────────────────────────
# 메인 라우터
# ─────────────────────────────────────────
if "gs" not in st.session_state:
    render_start()
elif st.session_state.gs.game_over or st.session_state.phase == "ending":
    render_ending()
else:
    render_sidebar()
    phase = st.session_state.phase
    if phase == "halftime":
        render_halftime()
    elif phase == "match_result":
        render_match_result()
    elif phase == "training":
        render_training()
