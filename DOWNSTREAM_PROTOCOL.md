# 下流ソフト開発プロトコル (Downstream Protocol)

Status: Housei 0.5.0 / 契約 `housei-sewing-plan/1.0.0`

このドキュメントは、**Housei で着付けが完了した衣服を、別の Blender
エクステンション(下流ソフト)が読み出すための手順**を定める。対象読者は、
ZOZO 以外のシミュレーターや他形式向けのエクスポータを開発する開発者。

正式なデータ仕様は `SEWING_PLAN_DESIGN.md`(縫いプラン)と
`HOU_DESIGN.md`(パーツメタデータ)にある。本書はその読み方の手順書である。

## 大原則

1. **界面は .blend 内のデータのみ。** 下流ソフトは Housei の Python
   モジュールを import してはならない(`bl_ext.<リポジトリ名>.<パッケージ>`
   のパスはインストール方法に依存し、壊れる)。カスタムプロパティと
   メッシュ属性がプロトコルの全部である。
2. **拒否せよ、修理するな。** 照合(後述)に失敗したら、理由と
   「Housei で 無重力着付 をやり直してください」を表示して停止する。
   下流でデータを補正・再構築してはならない。
3. **未知の JSON キーは無視する。** スキーマは追加キーで拡張されうる
   (`HOU` と同じ規約)。

## 前提条件

- Housei **0.5.0 以降**で **無重力着付(Zero GRAVITY)が成功**していること。
  成功時に縫いプランがコレクションへ保存される(ステータス欄に
  「縫いプラン(ペア N 組)を外部書き出し用に保存しました」と出る)。
- `.blend` が保存されていること。プロトコルの実体は保存されたファイルである。

## データの所在

| データ | 場所 |
|---|---|
| 衣服コレクション | `collection["housei_role"] == "clothes"` |
| 縫いプラン(頂点対応) | コレクションの `housei_sewing_plan_json`(JSON 文字列) |
| パーツの識別・メタデータ | オブジェクトの `HOU`(JSON 文字列、`HOU_DESIGN.md`) |
| 着付け後の形状 | メッシュ頂点 × `obj.matrix_world`(ワールド座標) |
| 平面型紙座標 | 頂点属性 `housei_pattern_position`(FLOAT_VECTOR/POINT) |
| 縫い目ラベルの辺 | エッジ属性 `sewing_<ラベル>`(BOOLEAN/EDGE)※通常は不要 |
| 型紙原文 | コレクションの `housei_document_json` ※通常は不要 |
| ボディ(衝突体) | **下流ソフト自身の UI で選択させる**(Housei は所有しない) |

## 単位・座標系

- 長さは**メートル**、空間は**ワールド座標**、上方向は **+Z**(Blender 準拠)。
- 型紙座標(`housei_pattern_position`)も**メートル**の平面座標である。
- オブジェクト変換の 3×3 部の行列式が負(反転)の場合、ワールド化で三角形の
  巻き方向が裏返る。法線の向きを使う下流は巻きを `(0, 2, 1)` に戻すこと
  (下のリファレンスリーダー参照。Housei 自身も同じ扱いをする)。

## 読み取り手順

1. **コレクションを見つける** — `housei_role == "clothes"` のコレクション。
   複数あるときはユーザーに選ばせる。
2. **縫いプランを読む** — `housei_sewing_plan_json` を JSON として解析し、
   `schema` が `housei-sewing-plan/1.` で始まることを確認。プロパティが
   無ければこの衣服はまだ縫われていない(または 0.5.0 より古い)。拒否する。
3. **パーツを解決する** — プランの `parts[i].object`(オブジェクト名)で
   コレクション内のメッシュを取得する。
4. **照合する(必須)** — 各パーツについて
   `len(mesh.vertices) == parts[i].vertices`、
   `housei_cut_scheme == parts[i].cut_scheme`、
   `housei_mesh_spacing_m ≒ parts[i].mesh_spacing_m` を確認。
   **1 つでも不一致なら拒否**(プランより後に裁断し直された衣服である。
   頂点番号はトポロジ変更を生き延びない)。
5. **ジオメトリを読む** — 各パーツのワールド頂点、三角形
   (`calc_loop_triangles`)、型紙座標を読み、下流側の連結順で連結する。
6. **縫いペアを展開する** — `pairs` はラベル別の
   `[パーツ番号, ローカル頂点, パーツ番号, ローカル頂点]`。パーツ番号は
   `parts` 配列の添字、頂点番号はそのパーツのメッシュ内ローカル番号。
   自分の連結順に合わせてオフセットを足すだけでよい。
7. **ボディ** — 下流ソフトの UI でメッシュを選択させる。

## リファレンスリーダー(検証済み)

以下は Housei を一切 import しない完全なリーダー実装である。参照衣服
(5 パーツ、35,301 頂点、縫いペア 1,129 組)で実行し、復号したペアの
ワールド距離が平均 1.8 mm(縫い済みなのでほぼ密着)であることを確認済み。

```python
import json

import bpy
import numpy as np

PLAN_PROPERTY = "housei_sewing_plan_json"


def read_sewn_garment(collection):
    """着付け済み衣服を .blend データだけから読み出す。

    返り値の positions / faces / pattern / seam_pairs は本関数の連結順
    (プランの parts 順)で整合する。単位はメートル、ワールド座標。
    """
    raw = collection.get(PLAN_PROPERTY)
    if not raw:
        raise RuntimeError(
            f"{collection.name} に縫いプランがありません。"
            "Housei で 無重力着付 を実行してから保存してください。"
        )
    plan = json.loads(raw)
    schema = str(plan.get("schema", ""))
    if not schema.startswith("housei-sewing-plan/1."):
        raise RuntimeError(f"未対応の縫いプラン schema です: {schema}")

    objects = []
    for entry in plan["parts"]:
        obj = collection.objects.get(entry["object"])
        if obj is None or obj.type != "MESH":
            raise RuntimeError(f"パーツ {entry['object']} が見つかりません。")
        mesh = obj.data
        stale = (
            len(mesh.vertices) != int(entry["vertices"])
            or int(obj.get("housei_cut_scheme", 0) or 0) != int(entry["cut_scheme"])
            or abs(
                float(obj.get("housei_mesh_spacing_m", 0.0))
                - float(entry["mesh_spacing_m"])
            ) > 1e-9
        )
        if stale:
            raise RuntimeError(
                f"{obj.name} は縫いプランより新しく裁断されています。"
                "Housei で 無重力着付 をやり直してください。"
            )
        objects.append(obj)

    position_blocks, face_blocks, pattern_blocks = [], [], []
    starts, offset = [], 0
    for obj in objects:
        mesh = obj.data
        count = len(mesh.vertices)
        local = np.empty((count, 3), dtype=np.float64)
        mesh.vertices.foreach_get("co", local.ravel())
        matrix = np.array(obj.matrix_world, dtype=np.float64)
        world = local @ matrix[:3, :3].T + matrix[:3, 3]

        mesh.calc_loop_triangles()
        triangles = np.empty((len(mesh.loop_triangles), 3), dtype=np.int64)
        mesh.loop_triangles.foreach_get("vertices", triangles.ravel())
        if np.linalg.det(matrix[:3, :3]) < 0.0:
            triangles = triangles[:, (0, 2, 1)]  # 反転変換は巻き方向を戻す

        attribute = mesh.attributes.get("housei_pattern_position")
        if attribute is None or len(attribute.data) != count:
            raise RuntimeError(f"{obj.name} に型紙座標がありません。")
        pattern = np.empty((count, 3), dtype=np.float64)
        attribute.data.foreach_get("vector", pattern.ravel())

        position_blocks.append(world)
        face_blocks.append(triangles + offset)
        pattern_blocks.append(pattern)
        starts.append(offset)
        offset += count

    seam_pairs, seam_labels = [], []
    for label, pairs in plan["pairs"].items():
        for slot_a, vertex_a, slot_b, vertex_b in pairs:
            seam_pairs.append((starts[slot_a] + vertex_a, starts[slot_b] + vertex_b))
            seam_labels.append(label)

    return {
        "parts": objects,
        "positions": np.concatenate(position_blocks),
        "faces": np.concatenate(face_blocks),
        "pattern": np.concatenate(pattern_blocks),
        "seam_pairs": np.asarray(seam_pairs, dtype=np.int64),
        "seam_labels": seam_labels,
    }
```

この返り値は Housei が自前のソルバーバックエンドへ渡す入力
(`solver_backend.py` の `SolveJob`)とほぼ同型である。つまり下流ソフトは、
Housei が ZOZO へ渡しているのと同じ内容を、同じ精度で受け取れる。

## してはならないこと

- Housei のモジュールを import する(理由は大原則 1)。
- 照合失敗を黙って補正する(縫いペアがずれた衣服は静かに壊れる)。
- パーツをオブジェクト名の見た目や並びから推測する(プランの `parts` が正)。
- Housei のオブジェクト・属性を書き換える(下流は読み取り専用)。
- `sewing_*` エッジ属性から縫いペアを自力で再導出する(リング対応・
  グループ完成判定を含む Housei 内部ロジックの複製になり、一致の保証がない。
  縫いプランを読むこと)。

## バージョニング

- `schema` のメジャーが `1` である限り、本書の手順で読める。追加キーは
  無視してよい。
- 読み手の挙動変更が必要になる変更はスキーマのバンプで示される。
  `SEWING_PLAN_DESIGN.md` が契約の原文である。

## 関連文書

- `SEWING_PLAN_DESIGN.md` — 縫いプランの正式仕様(契約)
- `HOU_DESIGN.md` — パーツ単位メタデータ `HOU` の仕様
- `README.md` — 製品ワークフロー(裁断 → 無重力着付 → 書き出し)
