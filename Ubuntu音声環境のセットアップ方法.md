# Ubuntu で静音ループバック文字起こしが安定する完全手順

このメモは、Windows 版の `Windows音声環境のセットアップ方法.md` と同じ役割を Ubuntu 版で果たすための手順書です。Ubuntu では VB-CABLE ではなく PipeWire/PulseAudio の `monitor source`、`module-null-sink`、`module-loopback` を使い、PC 再生音を Speechmatics へ送りながら同じ音をヘッドホンやスピーカーで聞ける状態を作ります。

既存の Ubuntu 版はすでに動作しているため、通常運用の安定性を崩さない設定を優先します。Speechmatics の高精度化は `SPEECHMATICS_OPERATING_POINT=enhanced` で切り替えられますが、まず `standard` で音声経路と利用枠を確認してから上げます。

---

## 1. 構成イメージ

### 1.1 通常の PipeWire monitor を使う流れ

Ubuntu では、多くの環境で出力デバイスごとに `Monitor of <sink>` が自動で用意されます。これは「その出力へ流れている音を録音入力として見せる」仕組みです。

```text
Edge / Chrome / Discord / Meet など
  出力先: ヘッドホン / スピーカー sink
        |
        +--> 実際のヘッドホン / スピーカー
        |      -> 自分の耳で聞く
        |
        +--> Monitor of <その出力デバイス>
               |
               +--> このツールが入力として拾う
                     -> Speechmatics
                     -> Google 翻訳
                     -> Web UI / ログ
```

この構成で `python -m transcriber.cli --diagnose-audio` に `pipewire` / `default` / `*.monitor` が候補として出ていれば、明示的な仮想 sink を作らなくても運用できます。

### 1.2 VB-CABLE に近い明示的な仮想 sink 構成

monitor が不安定な環境、または Windows 版の VB-CABLE と同じように「文字起こし対象の音だけを仮想経路へ入れたい」場合は、`scripts/setup_audio_loopback_linux.sh` で `codex_transcribe` を作ります。

```text
Edge / Chrome / Discord など
  出力先: codex_transcribe 仮想sink
        |
        v
  [PipeWire/PulseAudio module-null-sink]
        |
        +--> codex_transcribe.monitor
        |      |
        |      +--> このツールが入力として拾う
        |            -> Speechmatics
        |            -> Google 翻訳
        |            -> Web UI / ログ
        |
        +--> module-loopback
               再生先: 元のヘッドホン / スピーカー sink
               -> 自分の耳で聞く
```

| 役割 | Ubuntu / PipeWire | Windows 版で近いもの |
| --- | --- | --- |
| アプリが音を流し込む先 | `codex_transcribe` 仮想 sink | `CABLE Input` |
| 文字起こしツールが拾う入力 | `codex_transcribe.monitor` / `Monitor of <sink>` | `CABLE Output` |
| 自分の耳へ戻す経路 | `module-loopback` | 「このデバイスを聴く」 |
| 実際に聞く先 | 物理ヘッドホン / スピーカー sink | ヘッドホン / スピーカー |

---

## 2. 「sink」「source」「monitor」の意味

Ubuntu の音声設定では、Windows の `Input` / `Output` と同じように、見る立場によって言葉が混乱しやすいです。

| 種類 | ざっくりした意味 | 例 |
| --- | --- | --- |
| sink | アプリが音を流し込む再生先 | ヘッドホン、スピーカー、`codex_transcribe` |
| source | 録音ソフトが音を取り出す入力元 | マイク、`*.monitor`、`pipewire` |
| monitor source | sink に流れている音を録音元として見せるもの | `alsa_output...monitor`、`codex_transcribe.monitor` |

`AUDIO_LINUX_LOOPBACK_SINK` は録音元ではなく、聞き戻す先の物理 sink 名です。録音元は、その sink の monitor、または `pipewire` / `default` の入力候補になります。

---

## 3. 事前準備と診断

```bash
cd /home/yamada/Downloads/エスペラント音声文字起こし_Ubuntu2404_1103完成
source .venv311/bin/activate
python -m transcriber.cli --check-environment
python -m transcriber.cli --list-devices
python -m transcriber.cli --diagnose-audio
python -m transcriber.cli --audio-routing-guide
```

見るポイント:

- `--check-environment` で `All checks passed` が出る。
- `--list-devices` で `pipewire` / `default` / `*.monitor` が入力候補として見える。
- `--diagnose-audio` の「設定済みデバイス」が、現在の `.env` の `AUDIO_DEVICE_INDEX` と一致する。
- `--audio-routing-guide` に Linux ループバックの図が表示される。

`AUDIO_DEVICE_INDEX` は環境や USB/Bluetooth 機器の抜き差しで変わることがあります。固定値を写すのではなく、必ずこの環境の `--list-devices` で出た番号を使います。

---

## 4. Ubuntu 側のルーティング

### 4.1 通常運用

1. GNOME の「設定 → サウンド」で、実際に聞きたい出力先をヘッドホンまたはスピーカーにする。
2. `python -m transcriber.cli --test-audio-levels 3 --test-audio-profile loopback` を実行する。
3. 入力レベルが検出されれば、その monitor 経路は生きています。
4. 無音の場合は `pavucontrol` を開き、「録音」タブで `python -m transcriber.cli` の入力元を `Monitor of <出力デバイス>` にする。

`pavucontrol` が未導入なら以下で追加できます。

```bash
sudo apt install pavucontrol
```

### 4.2 `codex_transcribe` を明示的に作る場合

```bash
bash scripts/setup_audio_loopback_linux.sh
```

このスクリプトは以下を行います。

1. 物理ヘッドホン/スピーカー sink を検出する。
2. `codex_transcribe` という `module-null-sink` を作る。
3. `codex_transcribe.monitor` から物理 sink へ `module-loopback` を張る。
4. 既定再生を `codex_transcribe`、既定録音を `codex_transcribe.monitor` にする。

戻し先の物理 sink を明示したい場合は、以下のように指定します。

```bash
HEADPHONE_SINK=alsa_output.pci-0000_00_1f.3.analog-stereo bash scripts/setup_audio_loopback_linux.sh
```

`python -m transcriber.cli --easy-start` や `./easy_start.sh` から実行した場合は、CLI 側が元の既定 sink/source を覚えて、終了時に戻します。スクリプト単体で実行した場合は、必要に応じて以下で戻します。

```bash
bash scripts/reset_audio_defaults.sh
```

### 4.3 アプリ単位で流す音を分ける

Windows 版では音量ミキサーで Chrome だけを `CABLE Input` に固定します。Ubuntu では `pavucontrol` の「再生」タブで、再生中の Chrome / Edge / Discord などを `codex_transcribe` へ移動できます。

文字起こし対象だけを `codex_transcribe` に流し、通常の通知音や音楽アプリは物理ヘッドホン/スピーカーへ残すと、余計な音を Speechmatics に送らずに済みます。

---

## 5. `.env` の基本方針

通常は既存の安定運用を維持します。

```ini
TRANSCRIPTION_BACKEND=speechmatics
SPEECHMATICS_APP_ID=realtime
SPEECHMATICS_LANGUAGE=eo
SPEECHMATICS_CONNECTION_URL=wss://eu2.rt.speechmatics.com/v2
SPEECHMATICS_SAMPLE_RATE=16000
SPEECHMATICS_AUTH_MODE=temporary_key
SPEECHMATICS_OPERATING_POINT=standard

AUDIO_CAPTURE_MODE=loopback
AUDIO_DEVICE_INDEX=4
AUDIO_SAMPLE_RATE=16000
AUDIO_DEVICE_SAMPLE_RATE=48000
AUDIO_CHANNELS=1
AUDIO_CHUNK_DURATION_SECONDS=0.5
AUDIO_AUTO_SETUP_LOOPBACK=true
# AUDIO_LINUX_LOOPBACK_SINK=alsa_output.pci-0000_00_1f.3.analog-stereo

TRANSCRIPT_LOG_ENABLED=true
TRANSCRIPT_LOG_PATH=logs/meet-session.log
WEB_UI_ENABLED=true
WEB_UI_OPEN_BROWSER=true

TRANSLATION_ENABLED=true
TRANSLATION_SOURCE_LANGUAGE=eo
TRANSLATION_TARGETS=ja,ko
TRANSLATION_PROVIDER=google
GOOGLE_TRANSLATE_CREDENTIALS_PATH=/absolute/path/to/google-service-account.json
GOOGLE_TRANSLATE_MODEL=nmt
```

| キー | 役割 | 補足 |
| --- | --- | --- |
| `SPEECHMATICS_AUTH_MODE` | Speechmatics 認証方式 | Ubuntu 版の既存安定運用は `temporary_key` |
| `SPEECHMATICS_OPERATING_POINT` | 精度モード | `standard` は省略送信、`enhanced` は高精度モデルを明示要求 |
| `AUDIO_DEVICE_INDEX` | このツールが開く入力番号 | `--list-devices` の現在値を使う |
| `AUDIO_DEVICE_SAMPLE_RATE` | 実デバイスのサンプルレート | Ubuntu では 48 kHz が安定しやすい環境が多い |
| `AUDIO_SAMPLE_RATE` | Speechmatics へ送る内部処理レート | 16 kHz |
| `AUDIO_LINUX_LOOPBACK_SINK` | 聞き戻す物理 sink | 録音元ではない |

Speechmatics を高精度側へ切り替える場合:

```bash
python -m transcriber.cli --set-speechmatics-operating-point enhanced
```

標準へ戻す場合:

```bash
python -m transcriber.cli --set-speechmatics-operating-point standard
```

切り替え時は `.env.bak.*` が作られます。復元は `latest` も使えますが、音声設定と Speechmatics 設定のバックアップが混ざることがあるため、直前に表示されたバックアップ名を指定する方が確実です。

---

## 6. 運用フロー

1. ヘッドホンまたはスピーカーを選び、Ubuntu の音声設定で実際に聞こえることを確認する。
2. `source .venv311/bin/activate` で仮想環境を有効化する。
3. `python -m transcriber.cli --check-environment` を実行する。
4. `python -m transcriber.cli --diagnose-audio` でループバック候補を確認する。
5. `python -m transcriber.cli --test-audio-levels 3 --test-audio-profile loopback` で Speechmatics 接続前に音が入っているか確認する。
6. 高精度化する場合だけ `python -m transcriber.cli --set-speechmatics-operating-point enhanced` を実行する。
7. `python -m transcriber.cli --log-level=INFO` または `./easy_start.sh` で起動する。
8. Web UI が `http://127.0.0.1:8765` に開き、`logs/meet-session.log` に `Final:` 行が追記されることを確認する。

成功時のログ目安:

```text
[INFO] root: Starting transcription pipeline with backend=speechmatics.
[INFO] root: Starting audio stream on pipewire ... (device index <INDEX>)
[INFO] root: Audio stream started successfully
[INFO] root: Connected to Speechmatics realtime endpoint.
[INFO] root: Final: Saluton spektantoj.
```

---

## 7. モニタリングと検証方法

- `python -m transcriber.cli --diagnose-audio`: ループバック候補、既定再生、既定録音、設定済みデバイスを確認する。
- `python -m transcriber.cli --audio-routing-guide`: 現在の環境向けにルーティング図と戻し方を表示する。
- `python -m transcriber.cli --test-audio-levels 3 --test-audio-profile loopback`: 無音/クリッピング/入力レベルを短時間で確認する。
- `pavucontrol` の「録音」タブ: `python` の録音元が `Monitor of <sink>` になっているか確認する。
- `logs/meet-session.log`: `Final:` 行と翻訳文が継続して追記されるか確認する。
- Web UI: 字幕と翻訳がリアルタイムに流れるか確認する。

---

## 8. マイク入力を壊さないための運用

会議で自分が話すマイクと、相手側/PC側音声を文字起こしするループバックは分けて考えます。

1. Ubuntu の通常入力は実マイクのままで構いません。
2. Transcriber は `AUDIO_DEVICE_INDEX` で指定した monitor 入力を直接開きます。
3. Chrome / Discord / Meet のマイク入力を `codex_transcribe.monitor` にしないでください。相手へ送るマイク音声が壊れます。
4. PC音だけを文字起こししたい場合は、固定するのはアプリの「出力先」です。

---

## 9. トラブルシューティング

| 症状 | よくある原因 | 対処 |
| --- | --- | --- |
| 文字起こしが無音 | `pavucontrol` で録音元が物理マイクになっている | 「録音」タブで `Monitor of <sink>` を選ぶ |
| 自分の耳には聞こえるがツールが拾わない | 直接ヘッドホンへ出していて monitor 入力を開いていない | `--diagnose-audio` と `--test-audio-levels` で入力候補を確認 |
| ツールは拾うが耳で聞こえない | `module-loopback` の戻し先が違う、または物理 sink がミュート | `HEADPHONE_SINK=... scripts/setup_audio_loopback_linux.sh` または `pavucontrol` で戻し先を確認 |
| イヤホン挿抜で無音になる | WirePlumber が既定 source を物理マイクへ戻した | `pavucontrol` で monitor を選び直す。頻発する場合は `scripts/wp-force-monitor.sh` を使う |
| `AUDIO_DEVICE_INDEX` が外れる | USB/Bluetooth 機器の抜き差しで番号が変わった | `--list-devices` で番号を再確認し、`--apply-audio-profile loopback` を使う |
| ノイズやドロップが増える | 実デバイス 48 kHz と内部 16 kHz の扱いが曖昧 | `AUDIO_DEVICE_SAMPLE_RATE=48000`、`AUDIO_SAMPLE_RATE=16000` を明示 |
| enhanced で Speechmatics が拒否する | APIキー/契約/利用枠が enhanced realtime に未対応 | `--set-speechmatics-operating-point standard` で戻し、Speechmatics ポータルを確認 |
| `timelimit_exceeded` / close code `4006` | Speechmatics 側のリアルタイム利用枠・契約・APIキー状態 | 音声経路ではなく Speechmatics 側を確認し、必要なら close code と reason を添えて問い合わせる |

---

## 10. 他手法との比較

- **PipeWire/PulseAudio monitor**: 追加ソフトが少なく、Ubuntu では第一候補。物理出力ごとの monitor を拾う。
- **`module-null-sink` + `module-loopback`**: Windows 版 VB-CABLE に最も近い。対象音だけ仮想 sink へ分けやすい。
- **JACK / qpwgraph / Helvum**: 柔軟だが、日常運用には設定項目が多い。
- **物理ケーブルでの再入力**: 分かりやすいがノイズ・遅延・機材依存が増えるため、このツールでは優先しない。

---

## 11. 最終チェックリスト

1. `.venv311` が有効で `python -m transcriber.cli --check-environment` が成功する。
2. `python -m transcriber.cli --diagnose-audio` に `pipewire` / `default` / `*.monitor` がループバック候補として出る。
3. `.env` の `AUDIO_DEVICE_INDEX` が現在の入力候補番号と一致する。
4. `python -m transcriber.cli --test-audio-levels 3 --test-audio-profile loopback` で入力レベルが検出される。
5. ヘッドホンまたはスピーカーで同じ音が聞こえる。
6. 高精度運用なら `.env` が `SPEECHMATICS_OPERATING_POINT=enhanced`。戻す場合は `--set-speechmatics-operating-point standard`。
7. `logs/meet-session.log` に Speechmatics の `Final:` 行と翻訳文が追記される。

以上を満たせば、Ubuntu でも Windows 版 VB-CABLE 構成と同じ発想で、PC 出力を聞きながら Speechmatics + Google 翻訳へ安定して送れます。
