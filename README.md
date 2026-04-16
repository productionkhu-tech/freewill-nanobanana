# NanoBanana — AI Image Studio (Web)

Google Gemini API를 사용한 AI 이미지 생성 스튜디오.
Flask + Selenium 기반 웹 앱으로, 브라우저에서 네이티브 앱처럼 동작합니다.

## 설치

### 1. 의존성 설치
```
install.bat
```
또는 수동:
```
pip install -r requirements.txt
```

### 2. API 키 등록
```
setup_env.bat
```

필요한 환경변수:
| 변수 | 설명 |
|------|------|
| `GOOGLE_APPLICATION_CREDENTIALS` | 서비스 계정 JSON 파일 경로 |
| `NANOBANANA_PROJECT_ID` | Google Cloud 프로젝트 ID |
| `NANOBANANA_LOCATION` | Vertex AI 위치 (기본: global) |
| `NANOBANANA_STUDIO_KEY` | Google AI Studio API 키 |

### 3. 실행
```
python launcher.py
```

## 기능
- Gemini API 이미지 생성 (Vertex AI + AI Studio 이중 연동)
- 레퍼런스 이미지 (최대 14장, 드래그앤드롭, [Image N] 태그)
- 갤러리 (검색, 즐겨찾기, 2/4/8열)
- 프로젝트 저장/불러오기 (JSON)
- 커스텀 파일 네이밍
- 이미지 뷰어 (줌/팬)
- 시스템 트레이 (닫아도 백그라운드 유지)
- 자동 업데이트 (GitHub releases)

## 버전
현재: `v2026-04-1601`
