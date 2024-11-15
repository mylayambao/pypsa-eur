# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2020-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
"""

import geopandas as gpd
from _helpers import set_scenario_config
from OnshoreRegionData import OnshoreRegionData


def get_unit_conversion_factor(
    input_unit: str,
    output_unit: str,
    unit_scaling: dict = {"Wh": 1, "kWh": 1e3, "MWh": 1e6, "GWh": 1e9, "TWh": 1e12},
) -> float:

    if input_unit not in unit_scaling.keys():
        raise ValueError(
            f"Input unit {input_unit} not allowed. Must be one of {
                unit_scaling.keys()}"
        )
    elif output_unit not in unit_scaling.keys():
        raise ValueError(
            f"Output unit {output_unit} not allowed. Must be one of {
                unit_scaling.keys()}"
        )

    return unit_scaling[input_unit] / unit_scaling[output_unit]


if __name__ == "__main__":

    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake(
            "build_heat_source_potentials",
            clusters=48,
        )

    set_scenario_config(snakemake)

    regions_onshore = gpd.read_file(snakemake.input.regions_onshore)
    heat_source_utilisation_potential = gpd.read_file(
        snakemake.input.utilisation_potential
    )

    heat_source_technical_potential = OnshoreRegionData(
        onshore_regions=regions_onshore,
        data=heat_source_utilisation_potential,
        column_name=snakemake.params.fraunhofer_heat_sources[
            snakemake.params.heat_source
        ]["column_name"],
        scaling_factor=get_unit_conversion_factor(
            input_unit=snakemake.params.fraunhofer_heat_sources[
                snakemake.params.heat_source
            ]["unit"],
            output_unit="MWh",
        )
        / snakemake.params.fraunhofer_heat_sources[snakemake.params.heat_source][
            "full_load_hours"
        ],
    ).data_in_regions_scaled

    heat_source_technical_potential.to_csv(snakemake.output[0])
