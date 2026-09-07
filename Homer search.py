import pandas

df = pandas.read_parquet("C:/users/owner/place-picker/data/processed/census_places.parquet")

print(df.head().to_string(index=False))

print(
    df[df["place_name"].str.contains("Homer", case=False, na=False)]
    .to_string(index=False)
)