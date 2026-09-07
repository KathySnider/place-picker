import pandas as pd

df = pd.read_parquet("your-file.parquet")

print(
    df[df["name"].isin(
        ["Homer", "Palmer", "Valdez", "Wasilla", "Talkeetna"]
    )].to_string(index=False)
)