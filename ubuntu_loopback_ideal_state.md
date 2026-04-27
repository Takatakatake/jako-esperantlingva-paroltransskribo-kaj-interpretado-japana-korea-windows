# Ubuntu イヤホン運用 引き継ぎメモ

この文書は、Ubuntu 上で「イヤホンでもスピーカーでも PC 再生音を聞きながら、その同じ音声をリアルタイム文字起こしに送る」という理想的な状態を再現するための手順です。PipeWire/PulseAudio 監視と `.env` 設定、サウンド設定アプリ、付属スクリプトを組み合わせれば、どのマシンでも同一状況を再現できます。

より詳しい再現手順は `Ubuntu音声環境のセットアップ方法.md` を参照してください。このファイルは短い引き継ぎメモとチェックリストとして使います。

## 0. Linux 版ループバックの全体像

Windows 版の VB-CABLE と同じ発想で見ると、Linux 版では PipeWire/PulseAudio の仮想 sink と monitor source を使って、再生音を「文字起こし」と「自分の耳」の二手に分けます。

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

`codex_transcribe` は `scripts/setup_audio_loopback_linux.sh` が作る仮想出力先です。`codex_transcribe.monitor` が録音側の入力になり、`module-loopback` が同じ音を実際のヘッドホンやスピーカーへ戻します。

| 役割 | Linux での名前 | Windows 版で近いもの |
| --- | --- | --- |
| アプリが音を流し込む先 | `codex_transcribe` 仮想 sink | `CABLE Input` |
| 文字起こしツールが拾う入力 | `codex_transcribe.monitor` / `Monitor of <sink>` | `CABLE Output` |
| 自分の耳へ戻す経路 | `module-loopback` | 「このデバイスを聴く」 |
| 実際に聞く先 | 物理ヘッドホン / スピーカー sink | ヘッドホン / スピーカー |

通常の PipeWire 環境では、物理出力にも `Monitor of <sink>` が自動で存在します。そのため、明示的に `codex_transcribe` を作らなくても `pipewire` / `default` / `*.monitor` を入力候補として拾える場合があります。環境によって monitor が不安定な場合だけ、`scripts/setup_audio_loopback_linux.sh` で Windows 版 VB-CABLE に近い明示的な経路を作ります。

## 1. 事前準備

1. 依存パッケージ
   - `python3.11`, `pip`, `virtualenv`
   - `pavucontrol`（必須ではないが、録音ソース確認に便利）
   - `pactl`（PulseAudio/PipeWire 管理に使用。一般的な Ubuntu には標準で入っている）
2. 仮想環境
   ```bash
   python3.11 -m venv .venv311
   source .venv311/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. `.env` を以下の方針で編集（値は環境に応じて置き換え）
   ```ini
   TRANSCRIPTION_BACKEND=speechmatics
   SPEECHMATICS_LANGUAGE=eo
   SPEECHMATICS_SAMPLE_RATE=16000
   SPEECHMATICS_AUTH_MODE=temporary_key
   SPEECHMATICS_OPERATING_POINT=standard
   AUDIO_CAPTURE_MODE=loopback
   AUDIO_DEVICE_INDEX=4          # `python -m transcriber.cli --list-devices` で得た pipewire の番号
   AUDIO_SAMPLE_RATE=16000       # 内部処理レート
   AUDIO_DEVICE_SAMPLE_RATE=48000  # 実ハードのレート（48 kHz 推奨）
   AUDIO_CHANNELS=1
   AUDIO_CHUNK_DURATION_SECONDS=0.5
   ```
   - 16 kHz で Speechmatics に送る一方、デバイス実レートを 48 kHz に固定しておくとノイズ・ドロップを避けやすい（`docs/audio_loopback.md` と `.env` の既存コメントに沿う）。
   - `AUDIO_DEVICE_INDEX` には、`python -m transcriber.cli --list-devices` で得た `pipewire` / `default` / `*.monitor` の番号を指定する。`.env` に文字列の `pipewire` を直接入れるのではなく、一覧に出た番号を入れる。
   - `AUDIO_LINUX_LOOPBACK_SINK` を使う場合は、録音元ではなく「戻し先」の物理 sink 名（例: `alsa_output...analog-stereo`）を入れる。録音元はその `.monitor`、または `pipewire` / `default` の入力候補になる。
   - `SPEECHMATICS_OPERATING_POINT=standard` は `operating_point` を送らず既定モデルに任せる。高精度化する場合だけ `python -m transcriber.cli --set-speechmatics-operating-point enhanced` で切り替え、問題があれば `standard` に戻す。

## 2. サウンド設定の考え方

- GNOME の「設定 → サウンド」で任意の出力（アナログヘッドフォン／スピーカーなど）を選び、実際に音が鳴ることを確認する。PipeWire は出力シンクごとに `Monitor of <sink>` を用意するため、どの出力を選んでも monitor から同じ音を取得できる。
- 入力デバイスは通常どおりマイクのままで構わない。Transcriber は `AUDIO_DEVICE_INDEX` で指定した monitor を直接開くため、OS の入力設定に干渉しない。
- Bluetooth ヘッドセット利用時は HFP/HSP モードに落ちるとモノラル 16 kHz になるので、A2DP か有線を推奨（`Windows-VBCable  Ubuntu-pavucontrol.txt` の安定性メモを参照）。

## 3. 起動前の確認コマンド

```bash
source .venv311/bin/activate
python -m transcriber.cli --list-devices      # pipewire(default) の index を確認
python -m transcriber.cli --diagnose-audio    # 設定済みデバイスが pipewire になっているか確認
python -m transcriber.cli --audio-routing-guide
python -m transcriber.cli --test-audio-levels 3 --test-audio-profile loopback
```

診断レポートの「設定済みデバイス」が `#4 pipewire` など期待値なら準備完了。ループバック候補に `pipewire`/`default` が表示されない場合は PipeWire/PulseAudio サービスの再起動や `pactl info` での確認を行う。

## 4. 実行手順

1. イヤホンを装着するか、スピーカーを使用するかを選択し、再生したいアプリ（Zoom/Meet/YouTube 等）の音声を PC で流す。
2. 仮想環境が有効な shell で `python -m transcriber.cli --log-level=INFO` もしくは `./easy_start.sh` を実行。
3. 初回のみ `pavucontrol` を開き「録音」タブで `python -m transcriber.cli` の入力ソースが `Monitor of <出力デバイス>` になっているか確認。ほとんどの環境では自動で monitor が割り当てられる。何らかの理由でマイクが選ばれていたら monitor を選び直す。
4. CLI ログに `Capturing audio from device index 4 (pipewire)` のような行が出て、数秒後に Speechmatics の部分認識／確定認識ログが流れれば OK。
5. イヤホン着脱中も monitor が変わらないことを確認する。もし音が落ちたら `python -m transcriber.cli --diagnose-audio` を再実行し、`pavucontrol` でソースを再指定する。

## 5. 自動復旧と便利スクリプト

- **自動監視機能**: `docs/ubuntu_audio_troubleshooting.md` に記載の通り、アプリはデフォルト入力の変更や無音状態を定期監視し、必要なら再接続する。`AUDIO_DEVICE_CHECK_INTERVAL`（デフォルト 2 秒）で感度を調整できる。
- **既定ソース固定**: どうしても monitor が他の入力に切り替わる環境では、`install -Dm755 scripts/wp-force-monitor.sh ~/bin/wp-force-monitor.sh` を実行し、必要に応じて systemd user サービス化して monitor を常に再設定する。
- **設定リセット**: トラブル時は `bash scripts/reset_audio_defaults.sh` を実行して物理スピーカー/マイクを選び直し、ループバック用の `module-loopback`/`module-null-sink` をアンロードできる。
- **ループバック再構築**: もし monitor が作成されない環境（古い PulseAudio 等）では、`scripts/setup_audio_loopback_linux.sh` を使って `codex_transcribe` という null sink + monitor を明示的に生成し、出力先（HEADPHONE_SINK）をイヤホンに指定することで同じ理想状態を再現できる。
- **Speechmatics 精度切替**: `python -m transcriber.cli --set-speechmatics-operating-point enhanced` で高精度側へ切り替える。契約や利用枠で enhanced が使えない場合は Speechmatics 側のエラーになるため、`--set-speechmatics-operating-point standard` で戻す。切替時は `.env.bak.*` が作られる。

## 6. 検証チェックリスト

1. `pactl info | grep 'Default Source'` → monitor（例: `alsa_output.pci-0000_00_1f.3.analog-stereo.monitor`）になっている。
2. `python -m transcriber.cli --list-devices` → `pipewire`/`default` が IN/OUT デバイスとして見えている。
3. CLI ログに「no data」警告が出ていない。出た場合は `pavucontrol` で monitor を選び直す。
4. イヤホンを抜く → スピーカー再生 → イヤホンを再び挿す、の順に切り替えても認識が途切れない。
5. `logs/meet-session.log` に連続した書き込み（Transcript）が残っている。
6. 高精度運用にした場合は `.env` の `SPEECHMATICS_OPERATING_POINT=enhanced` を確認し、不安定なら `standard` に戻せることを確認する。

## 7. トラブル対処早見表

| 症状 | 想定原因 | 対処 |
| --- | --- | --- |
| イヤホン挿抜で無音になる | GNOME が既定入力をマイクに戻した | `pavucontrol` で monitor を選ぶ／`wp-force-monitor.sh` を実行 |
| ループバック候補に `pipewire` が出ない | PipeWire/PulseAudio が不安定 | `systemctl --user restart pipewire pipewire-pulse` |
| ノイズ・ドロップが増えた | サンプリング不一致 | `.env` の `AUDIO_DEVICE_SAMPLE_RATE=48000` を確認、会議アプリ側も 48 kHz に合わせる |
| 設定が仮想デバイスのまま残る | null-sink をアンロードしていない | `bash scripts/reset_audio_defaults.sh` で戻す |
| enhanced へ切り替えたら Speechmatics が拒否する | APIキー/契約/利用枠が enhanced realtime に未対応 | `python -m transcriber.cli --set-speechmatics-operating-point standard` で戻し、Speechmatics ポータルで権限と残枠を確認 |

---

この手順を守れば、配布先の Ubuntu 環境でも「イヤホンで聴きながら同じ音を文字起こしに回す」状態を素早く再現できる。疑問点があれば `docs/audio_loopback.md` と `docs/ubuntu_audio_troubleshooting.md` も併せて参照すること。
