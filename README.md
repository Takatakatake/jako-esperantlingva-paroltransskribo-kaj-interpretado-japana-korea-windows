# エスペラントリアルタイム文字起こし（Windows版）

このリポジトリは、Zoom や Google Meet で行われるエスペラント会話を Windows 上でリアルタイムに文字起こしするためのワークフローを提供します。Speechmatics のリアルタイム STT を中心に、Vosk / Whisper をバックアップとして切り替えられる構成になっており、ブラウザ字幕、Zoom Closed Caption、翻訳、Discord Webhook 投稿まで一括で扱えます。

## 1. 必要環境

- **OS**: Windows 10 以上（PowerShell が利用可能であること）
- **Python**: CPython 3.11+（3.12 でも動作確認済み。.venv311 という仮想環境名のままで問題ありません）
- **オーディオループバック**: VB-Audio Virtual Cable もしくは VoiceMeeter（環境によっては Stereo Mix でも可）
- **Speechmatics**: リアルタイム利用権限と API キー（または JWT）
- **Zoom**: Closed Caption 用 POST URL を取得できるホスト権限（Recall.ai などのブリッジ経由でも可）
- **任意**
  - Whisper を GPU で利用する場合は RTX 4070 クラス以上の NVIDIA GPU
  - 完全オフライン運用向けの Vosk Esperanto モデル（`vosk-model-small-eo-0.42` など）
  - Google Cloud Translation の認証情報、または LibreTranslate のエンドポイント

## 2. クイックスタート

Windows Terminal / PowerShell を開き、任意のディレクトリで次を実行します。

```powershell
git clone https://github.com/Takatakatake/esperanto_onsei_mojiokosi.git
Set-Location .\esperanto_onsei_mojiokosi
py -3.11 -m venv .venv311
.venv311\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

その後、エクスプローラーで `easy_start.cmd` または `easy_setup.cmd` をダブルクリックするか、PowerShell から次を実行します。

```powershell
.\easy_start.cmd
```

ヘルパーの処理内容:
1. `.venv311` が存在すれば自動的にアクティベート
2. `python -m transcriber.cli --easy-start` を実行し、依存関係と音声設定をチェック
3. プロンプトに従うだけで診断とパイプライン起動まで案内  
   - ループバック音声が未設定なら、案内に従って `scripts\setup_audio_loopback_windows.ps1` を再実行してください。

## 3. `.env` の設定

テキストエディタで `.env` を開き、最低限以下を設定してください。

```ini
SPEECHMATICS_API_KEY=sk_live_************************
SPEECHMATICS_CONNECTION_URL=wss://<region>.rt.speechmatics.com/v2
SPEECHMATICS_LANGUAGE=eo
ZOOM_CC_POST_URL=https://wmcc.zoom.us/closedcaption?...   # ホストから提供された URL を貼り付け
AUDIO_DEVICE_INDEX=8                                      # --list-devices の結果で確認
AUDIO_DEVICE_SAMPLE_RATE=48000
ZOOM_CC_ENABLED=true
TRANSLATION_ENABLED=true
TRANSLATION_TARGETS=ja,ko
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...  # 任意
```

必要に応じて調整する追加設定:

```ini
TRANSCRIPTION_BACKEND=speechmatics           # speechmatics / vosk / whisper
AUDIO_WINDOWS_LOOPBACK_DEVICE=CABLE Output (VB-Audio Virtual Cable)
WEB_UI_ENABLED=true
TRANSLATION_PROVIDER=google                  # google / libre
GOOGLE_TRANSLATE_CREDENTIALS_PATH=C:\path\to\service-account.json
WHISPER_MODEL_SIZE=medium                    # tiny / small / medium / large
WHISPER_DEVICE=auto                          # auto / cpu / cuda
WHISPER_COMPUTE_TYPE=default                 # default / float16 / int8_float16 など
TRANSCRIPT_LOG_PATH=logs\esperanto.log
VOSK_MODEL_PATH=C:\path\to\vosk-model-small-eo-0.42
```

## 4. 日常的な操作

手動で仮想環境を有効化する場合:

```powershell
.venv311\Scripts\activate
```

よく使う CLI オプション:

```powershell
python -m transcriber.cli --check-environment   # 依存関係・必須ファイル・.env のチェック
python -m transcriber.cli --list-devices        # WASAPI デバイス一覧を取得
python -m transcriber.cli --diagnose-audio      # 詳細な音声診断レポートを表示
python -m transcriber.cli --log-level=INFO      # パイプラインを起動
python -m transcriber.cli --backend=vosk        # バックエンドを一時的に切り替え
python -m transcriber.cli --setup-wizard        # Windows 向けセットアップガイド
python -m transcriber.cli --show-config         # 現在の設定を表示（機密値はマスク）
```

`WEB_UI_ENABLED=true` の場合、字幕ボードは **http://127.0.0.1:8765** で閲覧できます。`WEB_UI_OPEN_BROWSER=true` を設定すると起動時に既定ブラウザが自動で開きます。開かない場合は両フラグの設定とローカルファイアウォールの許可設定を確認してください。

停止は `Ctrl+C`。ログには `Final:` 行で最終文字起こしと、Zoom 連携結果が出力されます。

## 5. 音声と診断

- `--list-devices` の出力に VB-Audio / VoiceMeeter（あるいは Stereo Mix）の入力が含まれているか確認してください。
- Speechmatics 側で音声が受信されない場合は `python -m transcriber.cli --diagnose-audio` を再実行し、ループバック入力がミュートされていないかチェックします。
- 既定のデバイスを元に戻すときは **設定 > サウンド > 録音** から通常のマイクを既定に設定し直してください。

### 翻訳の動作確認

```powershell
python scripts\test_translation.py "Bonvenon al nia kunsido."
```

現在の `.env` 設定を使用して翻訳結果を表示します。

## 6. トラブルシューティング（Windows）

| 症状 | 対処方法 |
|------|----------|
| `ImportError: No module named sounddevice` | `.venv311\Scripts\activate` 後に `python -m pip install -r requirements.txt` を再実行する |
| Speechmatics への接続に失敗する | `SPEECHMATICS_API_KEY` と `SPEECHMATICS_CONNECTION_URL` を再確認し、ファイアウォールで WebSocket が許可されているか確認する |
| Zoom 字幕が表示されない | `ZOOM_CC_POST_URL` と `ZOOM_CC_ENABLED=true` を確認。401/403 は URL 失効のサイン。ホスト側で字幕機能が有効か確認する |
| Whisper が GPU を認識しない | NVIDIA ドライバを更新し、`WHISPER_DEVICE=cuda` を設定。CUDA 対応の PyTorch が入っているか確認する |
| ループバックデバイスが見つからない | `scripts\setup_audio_loopback_windows.ps1` を PowerShell から実行し、録音デバイスに表示された「Stereo Mix」や VB-Audio ケーブルを有効化する |
| ポート 8765 が使用中 | 既存の `python -m transcriber.cli` ウィンドウを閉じる。必要であれば `Stop-Process -Name python -Force`（多用は非推奨）でプロセスを停止し、ローカルファイアウォールの許可設定も確認する |

## 7. セキュリティ上の注意

- `.env.example` はマスク済みの雛形です。運用では `.env` に本番値を設定し、**絶対にコミットしない**でください。
- 誤って秘密情報を公開した場合は `git rm --cached` などで履歴から削除し、すぐにキーを再発行してください。状況によっては履歴の書き換え（`git filter-repo` や BFG Repo-Cleaner）が必要です。
- `.gitignore` に `.env` や `*.json` を追加し、認証情報がリポジトリに残らないようにしましょう。

## 8. ディレクトリ構成のハイライト

- `transcriber/cli.py` - `python -m transcriber.cli` のエントリーポイント。Easy Start や診断コマンドを提供
- `transcriber/audio.py` - WASAPI を用いた音声キャプチャと自動再接続ロジック
- `transcriber/asr/` - Speechmatics / Vosk / Whisper のストリーミングクライアント群
- `transcriber/pipeline.py` - 音声入力、バックエンド処理、Zoom 連携、翻訳、Discord、Web UI を統括
- `scripts/` - Windows 用ヘルパー（`easy_start.ps1`、`setup_audio_loopback_windows.ps1`、`check_environment.py`、`diagnose_audio.py`）
- `tests/` - 既存のユニットテスト（依存関係を入れたあとで `pytest` を実行可能）
- `web/` - ブラウザ字幕ボードの静的ファイル（CSS と JavaScript）

## 9. 今後の推奨アクション

1. Zoom 参加者にリアルタイム文字起こしを行う旨を事前に共有する
2. Speechmatics のカスタム辞書に固有名詞や専門用語を登録し、精度を向上させる
3. オフライン運用に備えて Vosk / Whisper の性能を計測し、適切なパラメータを決定する
4. 長時間運用する場合はタスクスケジューラや NSSM などでサービス化し、監視とログ運用を整備する
5. `.env` や翻訳用サービスアカウントの JSON はバージョン管理外で安全に保管し、定期的に見直す

README_en.md と合わせて、Windows 専用の手順が常に最新になるよう更新を歓迎します。改善案があれば Issue や Pull Request で共有してください。
