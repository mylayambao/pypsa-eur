# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2017-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Creates GIS shape files of the countries, exclusive economic zones and `NUTS3 <
https://en.wikipedia.org/wiki/Nomenclature_of_Territorial_Units_for_Statistics>
`_ areas.

Relevant Settings
-----------------

.. code:: yaml

    countries:

.. seealso::
    Documentation of the configuration file ``config/config.yaml`` at
    :ref:`toplevel_cf`

Inputs
------

- ``data/bundle/naturalearth/ne_10m_admin_0_countries.shp``: World country shapes

    .. image:: img/countries.png
        :scale: 33 %

- ``data/eez/World_EEZ_v12_20231025_gpkg/eez_v12.gpkg   ``: World `exclusive economic zones <https://en.wikipedia.org/wiki/Exclusive_economic_zone>`_ (EEZ)

    .. image:: img/eez.png
        :scale: 33 %

- ``data/bundle/NUTS_2013_60M_SH/data/NUTS_RG_60M_2013.shp``: Europe NUTS3 regions

    .. image:: img/nuts3.png
        :scale: 33 %

- ``data/bundle/nama_10r_3popgdp.tsv.gz``: Average annual population by NUTS3 region (`eurostat <http://appsso.eurostat.ec.europa.eu/nui/show.do?dataset=nama_10r_3popgdp&lang=en>`__)
- ``data/bundle/nama_10r_3gdp.tsv.gz``: Gross domestic product (GDP) by NUTS 3 regions (`eurostat <http://appsso.eurostat.ec.europa.eu/nui/show.do?dataset=nama_10r_3gdp&lang=en>`__)
- ``data/ch_cantons.csv``: Mapping between Swiss Cantons and NUTS3 regions
- ``data/bundle/je-e-21.03.02.xls``: Population and GDP data per Canton (`BFS - Swiss Federal Statistical Office <https://www.bfs.admin.ch/bfs/en/home/news/whats-new.assetdetail.7786557.html>`_ )

Outputs
-------

- ``resources/country_shapes.geojson``: country shapes out of country selection

    .. image:: img/country_shapes.png
        :scale: 33 %

- ``resources/offshore_shapes.geojson``: EEZ shapes out of country selection

    .. image:: img/offshore_shapes.png
        :scale: 33 %

- ``resources/europe_shape.geojson``: Shape of Europe including countries and EEZ

    .. image:: img/europe_shape.png
        :scale: 33 %

- ``resources/nuts3_shapes.geojson``: NUTS3 shapes out of country selection including population and GDP data.

    .. image:: img/nuts3_shapes.png
        :scale: 33 %

Description
-----------
"""

import logging
from functools import reduce
from itertools import takewhile
from operator import attrgetter

import country_converter as coco
import geopandas as gpd
import numpy as np
import pandas as pd
from _helpers import configure_logging, set_scenario_config
from shapely.geometry import MultiPolygon, Polygon
import unicodedata

logger = logging.getLogger(__name__)

cc = coco.CountryConverter()


POP_YEAR=2019

# REMAP SOURCE: https://ec.europa.eu/eurostat/documents/345175/629341/NUTS2021.xlsx
NUTS3_2016_2021_REMAP = {
    "UKK22": "UKK25",   # from 2016 to 2021
    "UKK21": "UKK24",
    "UKN10": "UKN0A",
    "UKN11": "UKN0B", 
    "UKN12": "UKN0C", 
    "UKN13": "UKN0D", 
    "UKN14": "UKN0E", 
    "UKN15": "UKN0F",
    "UKN16": "UKN0G",  
}
DROP_REGIONS = [
    "ES703",
    "ES704",
    "ES705",
    "ES706",
    "ES707",
    "ES708",
    "ES709",
    "ES630",
    "ES640",
    "FRY10",
    "FRY20",
    "FRY30",
    "NO0B1",
    "NO0B2",
    "PT200",
    "PT300",
]


def _simplify_polys(polys, minarea=0.1, tolerance=None, filterremote=True):
    if isinstance(polys, MultiPolygon):
        polys = sorted(polys.geoms, key=attrgetter("area"), reverse=True)
        mainpoly = polys[0]
        mainlength = np.sqrt(mainpoly.area / (2.0 * np.pi))
        if mainpoly.area > minarea:
            polys = MultiPolygon(
                [
                    p
                    for p in takewhile(lambda p: p.area > minarea, polys)
                    if not filterremote or (mainpoly.distance(p) < mainlength)
                ]
            )
        else:
            polys = mainpoly
    if tolerance is not None:
        polys = polys.simplify(tolerance=tolerance)
    return polys


def countries(naturalearth, country_list):
    df = gpd.read_file(naturalearth)

    # Names are a hassle in naturalearth, try several fields
    fieldnames = (
        df[x].where(lambda s: s != "-99") for x in ("ISO_A2", "WB_A2", "ADM0_A3")
    )
    df["name"] = reduce(lambda x, y: x.fillna(y), fieldnames, next(fieldnames)).str[:2]
    df.replace({"name": {"KV": "XK"}}, inplace=True)

    df = df.loc[
        df.name.isin(country_list) & ((df["scalerank"] == 0) | (df["scalerank"] == 5))
    ]
    s = df.set_index("name")["geometry"].map(_simplify_polys).set_crs(df.crs)

    return s


def eez(eez, country_list):
    df = gpd.read_file(eez)
    iso3_list = cc.convert(country_list, src="ISO2", to="ISO3")
    pol_type = ["200NM", "Overlapping claim"]
    df = df.query("ISO_TER1 in @iso3_list and POL_TYPE in @pol_type").copy()
    df["name"] = cc.convert(df.ISO_TER1, src="ISO3", to="ISO2")
    s = df.set_index("name").geometry.map(
        lambda s: _simplify_polys(s, filterremote=False)
    )
    s = s.to_frame("geometry").set_crs(df.crs)
    s.index.name = "name"
    return s


def country_cover(country_shapes, eez_shapes=None):
    shapes = country_shapes
    if eez_shapes is not None:
        shapes = pd.concat([shapes, eez_shapes])
    europe_shape = shapes.union_all()
    if isinstance(europe_shape, MultiPolygon):
        europe_shape = max(europe_shape.geoms, key=attrgetter("area"))
    return Polygon(shell=europe_shape.exterior)


def nuts3(country_shapes, nuts3, nuts3pop, nuts3gdp, ch_cantons, ch_popgdp):
    df = gpd.read_file(nuts3)
    df["geometry"] = df["geometry"].map(_simplify_polys)
    df = df.rename(columns={"NUTS_ID": "id"})[["id", "geometry"]].set_index("id")

    pop = pd.read_table(nuts3pop, na_values=[":"], delimiter=" ?\t", engine="python")
    pop = (
        pop.set_index(
            pd.MultiIndex.from_tuples(pop.pop("unit,geo\\time").str.split(","))
        )
        .loc["THS"]
        .map(lambda x: pd.to_numeric(x, errors="coerce"))
        .bfill(axis=1)
    )["2014"]

    gdp = pd.read_table(nuts3gdp, na_values=[":"], delimiter=" ?\t", engine="python")
    gdp = (
        gdp.set_index(
            pd.MultiIndex.from_tuples(gdp.pop("unit,geo\\time").str.split(","))
        )
        .loc["EUR_HAB"]
        .map(lambda x: pd.to_numeric(x, errors="coerce"))
        .bfill(axis=1)
    )["2014"]

    cantons = pd.read_csv(ch_cantons)
    cantons = cantons.set_index(cantons["HASC"].str[3:])["NUTS"]
    cantons = cantons.str.pad(5, side="right", fillchar="0")

    swiss = pd.read_excel(ch_popgdp, skiprows=3, index_col=0)
    swiss.columns = swiss.columns.to_series().map(cantons)

    swiss_pop = pd.to_numeric(swiss.loc["Residents in 1000", "CH040":])
    pop = pd.concat([pop, swiss_pop])
    swiss_gdp = pd.to_numeric(
        swiss.loc["Gross domestic product per capita in Swiss francs", "CH040":]
    )
    gdp = pd.concat([gdp, swiss_gdp])

    df = df.join(pd.DataFrame(dict(pop=pop, gdp=gdp)))

    df["country"] = (
        df.index.to_series().str[:2].replace(dict(UK="GB", EL="GR", KV="XK"))
    )

    excludenuts = pd.Index(
        (
            "FRA10",
            "FRA20",
            "FRA30",
            "FRA40",
            "FRA50",
            "PT200",
            "PT300",
            "ES707",
            "ES703",
            "ES704",
            "ES705",
            "ES706",
            "ES708",
            "ES709",
            "FI2",
            "FR9",
        )
    )
    excludecountry = pd.Index(("MT", "TR", "LI", "IS", "CY"))

    df = df.loc[df.index.difference(excludenuts)]
    df = df.loc[~df.country.isin(excludecountry)]

    manual = gpd.GeoDataFrame(
        [
            ["BA1", "BA", 3234.0],
            ["RS1", "RS", 6664.0],
            ["AL1", "AL", 2778.0],
            ["XK1", "XK", 1587.0],
        ],
        columns=["NUTS_ID", "country", "pop"],
        geometry=gpd.GeoSeries(),
        crs=df.crs,
    )
    manual["geometry"] = manual["country"].map(country_shapes.to_crs(df.crs))
    manual = manual.dropna()
    manual = manual.set_index("NUTS_ID")

    df = pd.concat([df, manual], sort=False)

    df.loc["ME000", "pop"] = 617.0

    return df


def normalise_text(text):
    """
    Removes diacritics from non-standard Latin letters, converts them to their
    closest standard 26-letter Latin equivalents, and removes asterisks (*) from the text.

    Args:
        text (str): Input string to normalize.

    Returns:
        str: Normalized string with only standard Latin letters and no asterisks.
    """
    # Normalize Unicode to decompose characters (e.g., č -> c + ̌)
    text = unicodedata.normalize('NFD', text)
    # Remove diacritical marks by filtering out characters of the 'Mn' (Mark, Nonspacing) category
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
    # Remove asterisks
    text = text.replace('*', '')
    # Optionally, ensure only ASCII characters remain
    text = ''.join(char for char in text if char.isascii())
    return text


def create_regions(
    country_list,
    nuts3_path,
    ba_adm1_path,
    md_adm1_path,
    ua_adm1_path,
    xk_adm1_path,
    offshore_shapes,
):
    """
    Create regions by processing NUTS and non-NUTS geographical shapes.

    Parameters:
        - country_list (list): List of country codes to include.
        - nuts3_path (str): Path to the NUTS3 2021 shapefile.
        - ba_adm1_path (str): Path to adm1 boundaries for Bosnia and  Herzegovina.
        - md_adm1_path (str): Path to adm1 boundaries for Moldova.
        - ua_adm1_path (str): Path to adm1 boundaries for Ukraine.
        - xk_adm1_path (str): Path to adm1 boundaries for Kosovo.
        - offshore_shapes (geopandas.GeoDataFrame): Geographical shapes of the exclusive economic zones.
    
    Returns:
        geopandas.GeoDataFrame: A GeoDataFrame containing the processed regions with columns:
            - id: Region identifier.
            - country: Country code.
            - name: Region name.
            - geometry: Geometrical shape of the region.
            - level1: Level 1 region identifier.
            - level2: Level 2 region identifier.
            - level3: Level 3 region identifier.
    """
    # Prepare NUTS shapes
    logger.info("Processing NUTS regions.")
    regions = gpd.read_file(nuts3_path)
    regions.loc[regions.CNTR_CODE == "EL", "CNTR_CODE"] = "GR" # Rename "EL" to "GR
    regions["NUTS_ID"] = regions["NUTS_ID"].str.replace("EL", "GR")
    regions.loc[regions.CNTR_CODE == "UK", "CNTR_CODE"] = "GB" # Rename "UK" to "GB"
    regions["NUTS_ID"] = regions["NUTS_ID"].str.replace("UK", "GB")

    # Only include countries in the config
    regions = regions.query("CNTR_CODE in @country_list")

    # Create new df
    regions = regions[["NUTS_ID", "CNTR_CODE", "NAME_LATN", "geometry"]]
     
    # Rename columns and add level columns
    regions = regions.rename(columns={"NUTS_ID": "id", "CNTR_CODE": "country", "NAME_LATN": "name"})
    
    # Normalise text
    regions["id"] = regions["id"].apply(normalise_text)
    
    regions["level1"] = regions["id"].str[:3]
    regions["level2"] = regions["id"].str[:4]
    regions["level3"] = regions["id"]

    # Non NUTS countries
    logger.info("Processing non-NUTS regions.")
    
    ba_adm1 = gpd.read_file(ba_adm1_path)
    md_adm1 = gpd.read_file(md_adm1_path)
    ua_adm1 = gpd.read_file(ua_adm1_path)
    xk_adm1 = gpd.read_file(xk_adm1_path)

    regions_non_nuts = pd.concat([ba_adm1, md_adm1, ua_adm1, xk_adm1])
    regions_non_nuts = regions_non_nuts.drop(columns=["osm_id"])

    # Normalise text
    regions_non_nuts["id"] = regions_non_nuts["id"].apply(normalise_text)
    regions_non_nuts["name"] = regions_non_nuts["name"].apply(normalise_text)

    # Add level columns
    regions_non_nuts["level1"] = regions_non_nuts["id"]
    regions_non_nuts["level2"] = regions_non_nuts["id"]
    regions_non_nuts["level3"] = regions_non_nuts["id"]

    # Clip regions by non-NUTS shapes
    regions["geometry"] = regions["geometry"].difference(regions_non_nuts.geometry.union_all())

    # Concatenate NUTS and non-NUTS regions
    logger.info("Harmonising NUTS and non-NUTS regions.")
    regions = pd.concat([regions, regions_non_nuts])
    regions.set_index("id", inplace=True)

    # Drop regions out of geographical scope
    regions = regions.drop(DROP_REGIONS, errors="ignore")

    # Clip regions by offshore shapes
    logger.info("Clipping regions by offshore shapes.")
    regions["geometry"] = regions["geometry"].difference(offshore_shapes.geometry.union_all())

    return regions


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake("build_shapes")
    configure_logging(snakemake)
    set_scenario_config(snakemake)

    # old nuts3
    old = gpd.read_file("/home/bobby/projects/development/pypsa-eur/resources/shapes/nuts3_shapes.geojson") \
        .set_index("index")

    # Offshore regions
    offshore_shapes = eez(snakemake.input.eez, snakemake.params.countries)
    offshore_shapes.reset_index().to_file(snakemake.output.offshore_shapes)

    # Onshore regions
    regions = create_regions(
        snakemake.params.countries,
        snakemake.input.nuts3_2021,
        snakemake.input.ba_adm1,
        snakemake.input.md_adm1,
        snakemake.input.ua_adm1,
        snakemake.input.xk_adm1,
        offshore_shapes,
    )

    # TODO: remove old code
    country_shapes = countries(snakemake.input.naturalearth, snakemake.params.countries)
    country_shapes = regions.groupby("country")["geometry"].apply(lambda x: x.union_all())
    country_shapes.crs = regions.crs
    country_shapes.reset_index().to_file(snakemake.output.country_shapes)

    europe_shape = gpd.GeoDataFrame(
        geometry=[country_cover(country_shapes, offshore_shapes.geometry)],
        crs=country_shapes.crs,
    )
    europe_shape.reset_index().to_file(snakemake.output.europe_shape)

    # POP
    eurostat_pop = pd.read_table(snakemake.input.eurostat_pop, na_values=[":"], delimiter=" ?\t", engine="python")
    eurostat_pop.set_index(
        pd.MultiIndex.from_tuples(eurostat_pop.pop("freq,unit,sex,age,geo\\TIME_PERIOD").str.split(",")),
        inplace=True,
    )
    eurostat_pop = (
        eurostat_pop.loc[("A", "NR", "T", "TOTAL")]
        .map(lambda x: pd.to_numeric(x, errors="coerce"))
        .bfill(axis=1)
    )[str(POP_YEAR)]
    
    # Keep only rows with index length equal to 5 digits (referring to NUTS3 regions)
    eurostat_pop = eurostat_pop[eurostat_pop.index.str.len() == 5]

    # Corrections for select regions, as the provided dataset does not completely match with the map due to changes from 2016 to 2021
    eurostat_pop = eurostat_pop.rename(NUTS3_2016_2021_REMAP)

    # Replace index strings starting with "UK" with "GB", and "EL" with "GR"
    eurostat_pop.index = eurostat_pop.index.str.replace("UK", "GB").str.replace("EL", "GR")

    # left_join regions
    regions["pop"] = eurostat_pop





    eurostat_pop = (
        
        .loc["THS"]
        .map(lambda x: pd.to_numeric(x, errors="coerce"))
        .bfill(axis=1)
    )["2014"]



    # TODO update 2024 NUTS mapping, double check, create dedicated PR
    # TODD remove redundant data and update downloader to directly access commission data
    nuts3_shapes = nuts3(
        country_shapes,
        snakemake.input.nuts3,
        snakemake.input.nuts3pop,
        snakemake.input.nuts3gdp,
        snakemake.input.ch_cantons,
        snakemake.input.ch_popgdp,
    )
    nuts3_shapes.reset_index().to_file(snakemake.output.nuts3_shapes)
