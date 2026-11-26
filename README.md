# Codex 세션 모니터

Codex에서 선택한 모델과 추론 강도, 첫 토큰까지 걸린 시간과 출력 토큰 속도를 장비별로 확인합니다. 로컬에서 쓰거나 내 ChatGPT 계정의 비공개 Sites에 여러 장비를 연결할 수 있습니다.

[설치 패키지 다운로드](https://github.com/jinyounghub/codex-session-monitor-kit/releases/latest)

## 장비에 설치

압축을 풀거나 저장소를 복제한 뒤 폴더를 계속 사용할 위치에 둡니다. 릴리스의 `SHA256SUMS`에 설치 패키지의 체크섬이 있습니다. 장비 수집기에는 Python 3.11 이상이 필요합니다. Sites를 처음 만들 때는 별도로 Node.js와 Sites가 활성화된 Codex가 필요합니다.

### Windows GUI

PowerShell에서 설치합니다. 기존 설치를 업데이트할 때도 새 패키지 폴더에서 같은 명령을 실행합니다.

```powershell
.\platforms\windows\setup.ps1
py -3 -B manage.py site-status
```

사용자 로그온 작업이 수집기를 시작합니다. 업데이트 시 이전 설치가 지정한 CLI 경로를 복구합니다. Windows GUI가 WSL에서 Codex를 실행하면 해당 배포판에 Ubuntu 절차를 적용합니다.

### Ubuntu GUI

수집기를 설정하고 사용자 로그인 자동 시작을 등록합니다. 설정 후 Codex GUI를 평소처럼 엽니다.

```sh
python3 -B manage.py configure
python3 -B manage.py enable-autostart
python3 -B manage.py site-status
```

기존 설치에서 GUI나 데스크톱 바로가기에 `codex-capture`를 직접 지정했다면 해당 설정을 지우고 Codex의 기본 실행기를 사용하세요.

### macOS

수집기를 설정한 뒤 평소처럼 Dock이나 응용 프로그램에서 Codex를 엽니다. 수집기는 로그인할 때 별도 프로세스로 시작합니다.

```sh
python3 -B manage.py configure
python3 -B manage.py enable-autostart
```

이전 버전에서 Chrome 연결이 끊겼다면 업데이트한 폴더에서 `enable-autostart`를 한 번 실행하세요. 수집기가 등록했던 실행 경로를 해제합니다. 이전 실행기로 열어 둔 Codex는 작업을 마친 뒤 완전히 종료하고 평소처럼 다시 엽니다. 기본 실행 경로로 이미 다시 연 앱은 그대로 사용할 수 있습니다.

`python3 -B manage.py launch-codex`로도 기본 앱을 열 수 있습니다. 앱을 다른 위치에 설치했다면 `CODEX_MONITOR_MAC_APP`에 앱 번들 경로를 지정합니다.

## 내 ChatGPT 계정의 Sites에 연결

Sites 없이도 이 장비의 화면을 열 수 있습니다. [로컬 대시보드](http://127.0.0.1:8765/)

여러 장비를 한 화면에서 보려면 **내 계정으로 소유한 Sites 한 개**를 만들고 그 사이트에 각 장비를 연결합니다. 아래 프롬프트를 Sites가 활성화된 Codex에서 이 패키지 폴더를 열고 붙여넣습니다.

```text
이 설치 패키지로 내 ChatGPT 계정의 소유자 전용 Codex 세션 모니터 Site를 만들고 이 장비를 연결해 줘.

Sites 도구와 스킬을 사용해. 먼저 node scripts/prepare-site.mjs <이 폴더 밖의 새 디렉터리>로 별도 Site 소스를 준비해. 내 계정의 새 비공개 Site를 만들고 반환된 프로젝트 ID를 그 별도 소스의 .openai/hosting.json에만 저장해. 데이터베이스 바인딩 DB를 유지해. 내가 이전에 이 모니터를 배포한 Site를 지정했다면 소유자 전용 권한을 확인하고 그 Site와 전용 소스를 재사용해.

get_site로 이 Site의 서비스 접속 키 siwc_bypass_bearer_token을 받아. site_metrics.py의 auth_headers(token)에 따라 X-Codex-Monitor-Key를 계산하고 이 값을 Sites 런타임 비밀 COLLECTOR_WRITE_KEY로 설정해. 계산은 메모리에서 수행해. 값은 SHA-256의 16진수 표현이고, 입력은 UTF-8 codex-session-monitor/v1 뒤에 NUL 한 바이트와 서비스 키를 붙인 값이야.

별도 소스에서 node scripts/build-site.mjs로 빌드한 뒤 Sites 스킬의 소스 푸시·패키징 절차를 실행하고, 소유자 전용으로 배포해. 서비스 키와 쓰기 키는 도구 메모리와 가려진 표준 입력에서만 다뤄. 개인 Site 설정과 권한 변경은 별도 Site 소스와 Sites 도구에서 처리해.

이 장비에 아직 수집기가 없으면 README의 해당 운영체제 설치 절차를 진행해. 배포가 끝나면 get_site에서 같은 Site의 실제 배포 URL과 서비스 키를 가져와 manage.py connect-site --url <배포 URL> --name <이 장비 이름> --token-stdin으로 연결해. 수집기 실행, 새 수신 시각, 공통 화면의 장비 이름을 확인해. GUI에서 짧은 요청을 실행하고 해당 턴의 선택 모델, 첫 토큰 시간과 토큰/초가 표시되는지 확인해.
```

추가 장비에서는 같은 Sites의 배포 URL과 서비스 키를 사용합니다. `manage.py connect-site --url <내 Site 주소> --name <장비 이름>`은 가려진 입력으로 키를 받습니다. Codex 도구로 연결할 때는 `--token-stdin`으로 직접 전달합니다. 보는 사람은 해당 Site를 소유한 ChatGPT 계정으로 로그인합니다.

## 측정값과 수집 범위

- **선택 모델**은 Codex가 기록한 모델과 추론 강도입니다. 세션 상세에서 각 턴의 선택값을 확인할 수 있습니다.
- **첫 토큰**은 Codex의 턴별 TTFT입니다. **토큰/초**는 서버가 보고한 출력 토큰을 각 모델 스트림의 관측된 생성 시간으로 나눈 추정치입니다.
- 각 장비는 Codex의 세션 기록과 OpenTelemetry 성능 이벤트를 읽습니다. Site에는 장비 이름, 채팅 제목, 가명 처리한 세션·턴 ID, 선택 모델과 속도 측정값을 보냅니다.

`manage.py site-status`는 장비 수집기 실행 여부와 마지막 Site 수신 시각을 보여줍니다. `manage.py local-dashboard off`로 로컬 웹 화면을 닫고 수집을 계속할 수 있습니다. `manage.py disconnect-site`로 장비의 Site 전송 연결을 해제합니다.

이 배포판은 자신의 Codex 세션을 모니터링하는 용도로 내려받아 설치하고 사용할 수 있습니다.
