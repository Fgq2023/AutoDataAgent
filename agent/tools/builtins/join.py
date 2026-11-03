"""Join two table artifacts with an explicit cardinality."""
from agent.tools.base import Tool, register
from agent.tools.builtins._io import read_df, write_df_artifact


@register
class JoinTool(Tool):
    name = "join"
    description = "Join two tables on a shared column, checking cardinality."
    required_args = {"left": str, "right": str, "on": str, "how": str, "cardinality": str, "name": str}
    optional_args = {"require_left_match": bool}
    input_contract = {
        "left": "left artifact name", "right": "right artifact name",
        "on": "shared key column", "how": "inner or left",
        "cardinality": "one_to_one, one_to_many or many_to_one",
        "require_left_match": "optional boolean, fail if any left row lacks a right match",
        "name": "output artifact name",
    }
    output_contract = {"kind": "dataframe", "columns": "joined columns"}
    artifact_args = ["left", "right"]

    def run(self, args, ctx):
        left = read_df(ctx.run_dir, args["left"])
        right = read_df(ctx.run_dir, args["right"])
        key, how, cardinality = args["on"], args["how"], args["cardinality"]
        if how not in ("inner", "left") or cardinality not in ("one_to_one", "one_to_many", "many_to_one"):
            raise ValueError("join requires a supported join type and explicit cardinality")
        if left[key].isna().any() or right[key].isna().any():
            raise ValueError("join keys must not be null")
        require_left_match = args.get("require_left_match", False)
        if not isinstance(require_left_match, bool):
            raise ValueError("require_left_match must be a boolean")
        unmatched = int((~left[key].isin(right[key])).sum())
        if require_left_match and unmatched:
            raise ValueError(f"{unmatched} unmatched left row(s) on join key {key!r}")
        result = left.merge(right, on=key, how=how, validate=cardinality)
        return [write_df_artifact(ctx.run_dir, args["name"], result,
                                  join_key=key, join_type=how, cardinality=cardinality,
                                  unmatched_left_rows=unmatched)]
