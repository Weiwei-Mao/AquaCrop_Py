#!/usr/bin/env python
# -*- coding: utf-8 -*-

# AquaCrop crop growth model

import os
import shutil
import sys
import math
import gc

import pcraster as pcr

import VirtualOS as vos
import Meteo as meteo
import C02
import Groundwater as groundwater
import CropParameters as cropPars
import SoilAndTopoParameters as soilPars
import InitialConditions as initCond

import logging
logger = logging.getLogger(__name__)

class LandCover(object):

    def __init__(self, configuration, currTimeStep, initialState = None):

        self._configuration = configuration
        self._modelTime = currTimeStep

        # clone map, land mask
        pcr.setclone(configuration.cloneMap)
        self.cloneMap = configuration.cloneMap
        self.landmask = vos.readPCRmapClone(configuration.globalOptions['landmask'],
                                            configuration.cloneMap,
                                            configuration.tmpDir,
                                            configuration.globalOptions['inputDir'],
                                            True)
        attr = vos.getMapAttributesALL(self.cloneMap)
        self.nLat = int(attr['rows'])
        self.nLon = int(attr['cols'])

        # Crop parameters
        self.crop_pars = cropPars.CropParameters(iniItems, landmask)
        self.crop_pars.read()
        self.crop_pars.compute_variables()
        
        # Irrigation management parameters
        self.irrigation_mgmt_pars = irriMgmtPars.IrrigationMgmtParameters(self._configuration, landmask)
        self.irrigation_mgmt_pars.read()

        # Field management parameters
        self.field_mgmt_pars = fieldMgmtPars.FieldMgmtParameters(self._configuration, landmask)
        self.field_mgmt_parss.read()
                
        # # Define variables
        # # TODO: take out of function
        # self.set_initial_conditions()

        # List state variables
        self.state_vars = ['DelayedGDDs','DelayedCDs','PctLagPhase','tEarlySen',
                           'DAP','PreAdj','CropMature','CropDead','Germination',
                           'PrematSenes','HarvestFlag','Stage','Fpre','Fpost',
                           'fpost_dwn','fpost_upp','Fpol','sCor1','sCor2',
                           'GrowthStage','CC','CCadj','CC_NS','CCadj_NS','B',
                           'B_NS','HI','HIadj','CCxAct','CCxAct_NS','CCxW',
                           'CCxW_NS','CCxEarlySen','rCor','Zroot','CC0adj']
        
    def set_initial_conditions(self):

        # TODO: initial conditions
        # self.init_conditions = initCond.InitialConditions(self._configuration, self._modelTime, self.crop)
        
        # Harvest index
        self.YieldForm = arr_zeros
        self.HIref = arr_zeros
        self.PctLagPhase = arr_zeros

        # Crop growth
        self.GDD = arr_zeros
        self.Ksw_Exp = arr_zeros
        self.Ksw_Sto = arr_zeros
        self.Ksw_Sen = arr_zeros
        self.Ksw_Pol = arr_zeros
        self.Ksw_StoLin = arr_zeros
        self.Kst_Bio = arr_zeros
        self.Kst_PolH = arr_zeros
        self.Kst_PolC = arr_zeros

    def getState(self):
        result = {}
        for var in self.state_vars:
            result[var] = vars(self)[var]
        return result

    def getPseudoState(self):
        pass

    def update(self, meteo, CO2, currTimeStep):
        """Function to update parameters for current crop grown as well 
        as counters pertaining to crop growth
        """

        # Update season counter for crops planted on current day
        cond1 = (self.CropSequence & (currTimeStep.doy == self.PlantingDate))
        self.initCond.reset()
        self.crop_pars.compute_water_productivity_adjustment_factor(C02)
        self.SeasonCounter[cond1] += 1

        # TODO: this is only necessary if SeasonCounter > 1
        self.crop_pars.update(self, currTimeStep, meteo)

        # Update growing season
        cond2 = (self.SeasonCounter > 0)
        cond3 = ((self.crop_pars.PlantingDate <= currTimeStep.doy) & (currTimeStep.doy <= self.crop_pars.HarvestDate))
        cond4 = (self.crop_pars.PlantingDate > self.crop_pars.HarvestDate)
        cond5 = ((self.crop_pars.PlantingDate <= currTimeStep.doy) | (currTimeStep.doy <= self.crop_pars.HarvestDate))
        self.GrowingSeason = ((cond2 & cond3) | (cond2 & cond4 & cond5))

        # Get index of crops currently grown
        I,J,K = np.ogrid[:self.nRotation,:self.nLat,:self.nLon]
        crop_index = (
            np.arange(0, self.nCrop)[:,None,None,None]
            * np.ones((self.nRotation, self.nLon, self.nLat))[None,:,:,:])
        crop_index *= self.GrowingSeason
        crop_index = np.max(crop_index, axis=0).astype(int)
        growing_season_index = np.any(self.GrowingSeason, axis=0)

        # Increment days after planting
        self.DAP[growing_season_index] += 1
        self.DAP[np.logical_not(growing_season_index)] = 0

        # Increment growing degree days
        self.growing_degree_day(meteo)
        self.GDDcum[growing_season_index] += self.GDD[growing_season_index]
        self.GDDcum[np.logical_not(growing_season_index)] = 0

        # Select parameters for current day
        for nm in self.crop_pars.parameter_names:
            param = getattr(self.crop, nm)
            param = param[:,None,:,:] * np.ones((self.nRotation))[None,:,None,None]
            vars(self)[nm] = param[crop_index,I,J,K]

        for nm in self.irrigation_mgmt_pars.parameter_names:
            vars(self)[nm] = getattr(self.irrigation_mgmt, nm)[crop_index,I,J,K]

        for nm in self.field_mgmt_pars.parameter_names:
            vars(self)[nm] = getattr(self.field_mgmt, nm)[crop_index,I,J,K]
        
    def germination(self):
        """Function to check if crop has germinated"""

        # Expand soil properties to compartments
        th_fc = self.soil.th_fc[self.soil.layerIndex,:]
        th_wp = self.soil.th_wp[self.soil.layerIndex,:]

        # Add rotation, lat, lon dimensions to dz and dzsum
        arr_ones = np.ones((self.nRotation, self.nLat, self.nLon))
        dz = self.soil.dz[:,None,None,None] * arr_ones
        dzsum = self.soil.dzsum[:,None,None,None] * arr_ones

        # Add compartment dimension to zGerm
        zgerm = self.soil.zGerm[None,:,:,:] * np.ones((self.nComp))[:,None,None,None]

        # Here we force zGerm to have a maximum value equal to the depth of the
        # deepest soil compartment
        zgerm[zgerm > np.sum(self.soil.dz)] = np.sum(self.soil.dz)
        
        # Find compartments covered by top soil layer affecting germination
        comp_sto = ((dzsum - dz) < zgerm)

        # Calculate water content in top soil layer
        arr_zeros = np.zeros((self.nComp, self.nRotation, self.nLat, self.nLon))
        Wr_comp = arr_zeros
        WrFC_comp = arr_zeros
        WrWP_comp = arr_zeros

        # Determine fraction of compartment covered by top soil layer
        factor = 1 - ((dzsum - zgerm) / dz) 
        factor = np.clip(factor, 0, 1) * growing_season_index * comp_sto

        # Increment water storages (mm)
        Wr_comp = (factor * 1000 * self.th * dz)
        Wr_comp = np.clip(Wr_comp, 0, None)
        Wr = np.sum(Wr_comp, axis=0)
        WrFC_comp = (factor * 1000 * th_fc * dz)
        WrFC = np.sum(WrFC_comp, axis=0)
        WrWP_comp = (factor * 1000 * th_wp * dz)
        WrWP = np.sum(WrWP_comp, axis=0)

        # Calculate proportional water content
        WcProp = 1 - ((WrFC - Wr) / (WrFC - WrWP))

        # Check if water content is above germination threshold
        cond4 = (growing_season_index
                 & (WcProp >= self.GermThr)
                 & (np.logical_not(self.Germination)))
        self.Germination[cond4] = True

        # Increment delayed growth time counters if germination is yet to occur
        cond5 = (growing_season_index & (np.logical_not(self.Germination)))
        self.DelayedCDs[cond5] += 1
        self.DelayedGDDs[cond5] += self.GDD[cond5]
                 
        # Not in growing season so no germination calculation is performed
        self.Germination[np.logical_not(growing_season_index)] = False
        self.DelayedCDs[np.logical_not(growing_season_index)] = 0
        self.DelayedGDDs[np.logical_not(growing_season_index)] = 0

    def growth_stage(self):
        """Function to calculate number of growing degree days on 
        current day
        """
        if self.crop.CalendarType == 1:
            tAdj = self.DAP - self.DelayedCDs
        elif self.crop.CalendarType == 2:
            tAdj = self.GDDcum - self.DelayedGDDs

        # Update growth stage
        cond1 = (growing_season_index & (tAdj <= self.Canopy10Pct))
        cond2 = (growing_season_index & (tAdj <= self.MaxCanopy))
        cond3 = (growing_season_index & (tAdj <= self.Senescence))
        cond4 = (growing_season_index & (tAdj > self.Senescence))

        self.GrowthStage[cond1] = 1
        self.GrowthStage[cond2] = 2
        self.GrowthStage[cond3] = 3
        self.GrowthStage[cond4] = 4
        self.GrowthStage[np.logical_not(growing_season_index)] = 0

    def root_development(self, groundwater):
        """Function to calculate root zone expansion"""

        cond1 = growing_season_index
        
        # If today is the first day of season, root depth is equal to minimum depth
        cond1 = (growing_season_index & (self.DAP == 1))
        self.Zroot[cond1] = self.Zmin[cond1]

        # Adjust time for any delayed development
        if self.CalendarType == 1:
            tAdj = (self.DAP - self.DelayedCDs)
        elif self.CalendarType == 2:
            tAdj = (self.GDDcum - self.DelayedGDDs)
            
        # Calculate root expansion
        Zini = self.Zmin * (self.PctZmin / 100)
        t0 = np.round(self.Emergence / 2)
        tmax = self.MaxRooting
        if self.CalendarType == 1:
            tOld = (tAdj - 1)
        elif self.CalendarType == 2:
            tOld = (tAdj - self.GDD)

        tAdj[np.logical_not(growing_season_index)] = 0
        tOld[np.logical_not(growing_season_index)] = 0

        # Potential root depth on previous day
        ZrOld = np.zeros((self.nRotation, self.nLat, self.nLon))
        cond2 = (growing_season_index & (tOld >= tmax))
        ZrOld[cond2] = self.Zmax[cond2]
        cond3 = (growing_season_index & (tOld <= t0))
        ZrOld[cond3] = Zini[cond3]
        cond4 = (growing_season_index & (np.logical_not(cond2 | cond3)))
        X = (tOld - t0) / (tmax - t0)
        ZrOld[cond4] = ((Zini + (self.Zmax - Zini))
                        * X ** (1 / self.fshape_r))[cond4]
        
        cond5 = (growing_season_index & (ZrOld < self.Zmin))
        ZrOld[cond5] = self.Zmin[cond5]

        # Potential root depth on current day
        Zr = np.zeros((self.nRotation, self.nLat, self.nLon))
        cond6 = (growing_season_index & (tAdj >= tmax))
        Zr[cond6] = self.Zmax[cond6]
        cond7 = (growing_season_index & (tAdj <= t0))
        Zr[cond7] = Zini[cond7]
        cond8 = (growing_season_index & (np.logical_not(cond6 | cond7)))
        ZrOld[cond8] = ((Zini + (self.Zmax - Zini))
                        * X ** (1 / self.fshape_r))[cond8]
        
        cond9 = (growing_season_index & (Zr < self.Zmin))
        Zr[cond9] = self.Zmin[cond9]
        
        # Determine rate of change, adjust for any stomatal water stress
        dZr = Zr - ZrOld
        cond10 = (growing_season_index & (self.TrRatio < 0.9999))
        cond101 = (cond10 & (self.fshape_ex >= 0))        
        dZr[cond101] = (dZr * self.TrRatio)[cond101]
        cond102 = (cond10 & np.logical_not(cond101))
        fAdj = ((np.exp(self.TrRatio * self.fshape_ex) - 1)
                / (np.exp(self.fshape_ex) - 1))
        dZr[cond102] = (dZr * fAdj)[cond102]

        # Adjust root expansion for failure to germinate (roots cannot expand
        # if crop has not germinated)
        dZr[np.logical_not(self.Germination)] = 0

        # Get new rooting depth
        self.Zroot[growing_season_index] = (self.Zroot + dZr)[growing_season_index]

        cond11 = (growing_season_index & (self.soil.zRes > 0))
        cond111 = (cond11 & (self.Zroot > self.soil.zRes))
        self.rCor[cond111] = (
            (2 * (self.Zroot / self.soil.zRes)
             * ((self.SxTop + self.SxBot) / 2)
             - self.SxTop) / self.SxBot)[cond111]
        
        self.Zroot[cond111] = self.soil.zRes[cond111]

        # Limit rooting depth if groundwater table is present (roots cannot
        # develop below the water table)
        zGW = groundwater.zGW[None,:,:] * np.ones((nr))[:,None,None]
        cond12 = (growing_season_index & groundwater.WaterTable & (zGW > 0))
        cond121 = (cond12 & (self.Zroot > zGW))
        self.Zroot[cond121] = zGW[cond121]
        cond1211 = (cond121 & (self.Zroot < self.Zmin))
        self.Zroot[cond1211] = self.Zmin[cond1211]

        # No root system outside of growing season
        self.Zroot[np.logical_not(growing_season_index)] = 0

    def water_stress(self, meteo, soil_water, beta):
        """Function to calculate water stress coefficients"""        
        p_up = np.concatenate(
            (self.p_up1[None,:],
             self.p_up2[None,:],
             self.p_up3[None,:],
             self.p_up4[None,:]), axis=0)

        p_lo = np.concatenate(
            (self.p_lo1[None,:],
             self.p_lo2[None,:],
             self.p_lo3[None,:],
             self.p_lo4[None,:]), axis=0)
        
        fshape_w = np.concatenate(
            (self.fshape_w1[None,:],
             self.fshape_w2[None,:],
             self.fshape_w3[None,:],
             self.fshape_w4[None,:]), axis=0)
        
        # Adjust stress thresholds for Et0 on current day (don't do this for
        # pollination water stress coefficient)

        et0 = (meteo.referencePotET[None,:,:] * np.ones((self.nRotation))[:,None,None])
        cond1 = (self.ETadj == 1)
        for stress in range(3):
            p_up[stress,:][cond1] = (
                p_up[stress,:]
                + (0.04 * (5 - et0))
                * (np.log10(10 - 9 * p_up[stress,:])))[cond1]
            
            p_lo[stress,:][cond1] = (
                p_lo[stress,:]
                + (0.04 * (5 - et0))
                * (np.log10(10 - 9 * p_lo[stress,:])))[cond1]

        # Adjust senescence threshold if early senescence triggered
        if beta:
            cond2 = (self.tEarlySen > 0)
            p_up[2,:][cond2] = (p_up[2,:] * (1 - (self.beta / 100)))[cond2]

        # Limit adjusted values
        p_up = np.clip(p_up, 0, 1)
        p_lo = np.clip(p_lo, 0, 1)

        # Calculate relative depletion
        Drel = np.zeros((4, self.nRotation, self.nLat, self.nLon))
        # No water stress
        cond1 = (self.Dr <= (p_up * self.TAW))
        Drel[cond1] = 0
        
        # Partial water stress
        cond2 = (soil_water.Dr >  (p_up * soil_water.TAW)) & (soil_water.Dr < (p_lo * soil_water.TAW))
        Drel[cond2] = (1 - ((p_lo - (soil_water.Dr / soil_water.TAW)) / (p_lo - p_up)))[cond2]
        
        # Full water stress
        cond3 = (soil_water.Dr >= (p_lo * soil_water.TAW))
        Drel[cond3] = 1         

        # Calculate root zone stress coefficients
        idx = np.arange(0,3)
        Ks = (1 - ((np.exp(Drel[idx,:] * fshape_w[idx,:]) - 1)
                   / (np.exp(fshape_w[idx,:]) - 1)))

        # Water stress coefficients (leaf expansion, stomatal closure,
        # senescence, pollination failure)
        self.Ksw_Exp = Ks[0,:]
        self.Ksw_Sto = Ks[1,:]
        self.Ksw_Sen = Ks[2,:]
        self.Ksw_Pol = 1 - Drel[3,:]

        # Mean water stress coefficient for stomatal closure
        self.Ksw_StoLin = 1 - Drel[1,:]

    def canopy_cover_development(self, CC0, CCx, CGC, CDC, dt, Mode):
        """Function to calculate canopy cover development by end of the 
        current simulation day
        """
        if Mode == 'Growth':
            CC = (CC0 * np.exp(CGC * dt))
            cond1 = (CC > (CCx / 2))
            CC[cond1] = (CCx - 0.25 * (CCx / CC0) * CCx * np.exp(-CGC * dt))[cond1]
            CC = np.clip(CC, None, CCx)
        elif Mode == 'Decline':
            CC = np.zeros((CC0.shape))
            cond2 = (CCx >= 0.001)
            CC[cond2] = (CCx * (1 - 0.05 * (np.exp(dt * (CDC / CCx)) - 1)))[cond2]

        CC = np.clip(CC, 0, 1)
        return CC

    def canopy_cover_required_time(self, CCprev, CC0, CCx, CGC, CDC, dt, tSum, Mode):
        """Function to find required time to reach CC at end of previous 
        day, given current CGC or CDC
        """
        if Mode == 'CGC':
            CGCx = np.zeros((self.nRotation, self.nLat, self.nLon))
            cond1 = (CCprev <= (CCx / 2))
            CGCx[cond1] = ((np.log(CCprev / CC0)) / (tSum - dt))[cond1]
            cond2 = np.logical_not(cond1)
            CGCx[cond2] = (
                (np.log((0.25 * CCx * CCx / CC0) / (CCx - CCprev)))
                / (tSum - dt))[cond2]
            tReq = (tSum - dt) * (CGCx / CGC)
        elif Mode == 'CDC':
            tReq = ((np.log(1 + (1 - CCprev / CCx) / 0.05)) / (CDC / CCx))

        return tReq
                    
    def adjust_CCx(self, CCprev, CC0, CCx, CGC, CDC, dt, tSum):
        """Function to adjust CCx value for changes in CGC due to water 
        stress during the growing season
        """
        # Get time required to reach CC on previous day, then calculate
        # adjusted CCx
        tCCtmp = self.canopy_cover_required_time(CCprev, CC0, CCx, CGC,
                                                 CDC, dt, tSum, 'CGC')
        cond1 = (tCCtmp > 0)
        tCCtmp[cond1] += ((self.CanopyDevEnd - tSum) + dt)[cond1]
        CCxAdj = self.canopy_cover_development(CC0, CCx, CGC,
                                               CDC, tCCtmp, 'Growth')
        CCxAdj[np.logical_not(cond1)] = 0
        return CCxAdj

    def update_CCx_and_CDC(self, CCprev, CDC, CCx, dt):
        """Function to update CCx and CDC parameter values for 
        rewatering in late season of an early declining canopy
        """
        CCxAdj = CCprev / (1 - 0.05 * (np.exp(dt * (CDC / CCx)) - 1))
        CDCadj = CDC * (CCxAdj / CCx)
        return CCxAdj,CDCadj

    def canopy_cover(self, meteo, soil_water):
        """Function to simulate canopy growth/decline"""

        # TODO: call root_zone_water() before running canopy_cover()
        
        # Preallocate some variables
        CCxAdj = np.zeros((self.nRotation, self.nLat, self.nLon))
        CDCadj = np.zeros((self.nRotation, self.nLat, self.nLon))
        CCsen = np.zeros((self.nRotation, self.nLat, self.nLon))

        # Store initial condition
        self.CCprev = self.CC
        
        # Calculate root zone water content, and determine if water stress is
        # occurring
        # self.root_zone_water()
        self.water_stress(meteo, soil_water, beta = True)

        # Get canopy cover growth over time
        if self.crop.CalendarType == 1:
            tCC = self.DAP
            dtCC = np.ones((self.nRotation, self.nLat, self.nLon))
            tCCadj = self.DAP - self.DelayedCDs
        elif self.crop.CalendarType == 2:
            tCC = self.GDDcum
            dtCC = self.GDD
            tCCadj = self.GDDcum - self.DelayedGDDs

        # ######################################################################
        # Canopy development (potential)

        # No canopy development before emergence/germination or after maturity
        cond1 = (growing_season_index
                 & ((tCC < self.Emergence) | (np.round(self.Emergence) > self.Maturity)))
        self.CC_NS[cond1] = 0

        # Canopy growth can occur
        cond2 = (growing_season_index & (tCC < self.CanopyDevEnd))

        # Very small initial CC as it is first day or due to senescence. In this
        # case assume no leaf expansion stress
        cond21 = (cond2 & (self.CC_NS <= self.CC0))
        self.CC_NS[cond21] = (self.CC0 * np.exp(self.CGC * dtCC))[cond21]
        
        # Canopy growing
        cond22 = (cond2 & np.logical_not(cond21))
        tmp_tCC = tCC - self.Emergence
        tmp_CC = self.canopy_cover_development(self.CC0, self.CCx, self.CGC, self.CDC, tmp_tCC, 'Growth')
        self.CC[cond22] = tmp_CC[cond22]

        # Update maximum canopy cover size in growing season
        self.CCxAct_NS[cond2] = self.CC_NS[cond2]
        
        # No more canopy growth is possible or canopy in decline
        cond3 = (growing_season_index & (tCC > self.CanopyDevEnd))
        # Set CCx for calculation of withered canopy effects
        self.CCxW_NS[cond3] = self.CCxAct_NS[cond3]
        
        # Mid-season stage - no canopy growth, so do not update CC_NS
        cond31 = (cond3 & (tCC < self.Senescence))
        self.CCxAct_NS[cond31] = self.CC_NS[cond31]

        # Late-season stage - canopy decline
        cond32 = (cond3 & np.logical_not(cond31))
        tmp_tCC = tCC - self.Emergence
        tmp_CC = self.canopy_cover_development(self.CC0, self.CCx, self.CGC, self.CDC, tmp_tCC, 'Decline')
        self.CC[cond32] = tmp_CC[cond32]

        # ######################################################################
        # Canopy development (actual)

        # No canopy development before emergence/germination or after maturity
        cond4 = (growing_season_index
                 & ((tCCadj < self.Emergence) | (np.round(tCCadj) > self.Maturity)))
        self.CC[cond4] = 0

        # Canopy growth can occur
        cond5 = (growing_season_index
                 & (tCCadj < self.CanopyDevEnd)
                 & np.logical_not(cond4))
        cond51 = (cond5 & (self.CCprev <= self.CC0adj))

        # Very small initial CC as it is first day or due to senescence. In
        # this case, assume no leaf expansion stress
        self.CC[cond51] = (self.CC0adj * np.exp(self.CGC * dtCC))[cond51]

        # Canopy growing
        cond52 = (cond5 & np.logical_not(cond51))

        # Canopy approaching maximum size
        cond521 = (cond52 & (self.CCprev >= (0.9799 * self.CCx)))
        tmp_tCC = tCC - self.Emergence
        tmp_CC = self.canopy_cover_development(self.CC0, self.CCx, self.CGC, self.CDC, tmp_tCC, 'Growth')
        self.CC[cond521] = tmp_CC[cond521]
        self.CC0adj[cond521] = self.CC0[cond521]

        # Adjust canopy growth coefficient for leaf expansion water stress
        # effects
        cond522 = (cond52 & np.logical_not(cond521))
        CGCadj = self.CGC * self.Ksw_Exp

        # Adjust CCx for change in CGC
        cond5221 = (cond522 & (CGCadj > 0))
        tmp_CCxAdj = self.adjust_CCx(self.CCprev, self.CC0adj, self.CCx,
                                        CGCadj, self.CDC, dtCC, tCCadj)
        CCxAdj[cond5221] = tmp_CCxAdj[cond5221]

        cond52211 = (cond5221 & (CCxAdj > 0))

        # Approaching maximum canopy size
        cond522111 = (cond52211 & (np.abs(self.CCprev - self.CCx) < 0.00001))
        tmp_tCC = tCC - self.Emergence
        tmp_CC = self.canopy_cover_development(self.CC0, self.CCx, self.CGC, self.CDC, tmp_tCC, 'Growth')
        self.CC[cond522111] = tmp_CC[cond522111]

        # Determine time required to reach CC on previous day, given CGCadj
        # value
        cond522112 = (cond52211 & np.logical_not(cond522111))
        tReq = self.canopy_cover_required_time(self.CCprev, self.CC0adj, CCxAdj,
                                       CGCadj, self.CDC, dtCC, tCCadj, 'CGC')
        tmp_tCC = tReq + dtCC

        # Determine new canopy size
        cond5221121 = (cond522112 & (tmp_tCC > 0))
        tmp_CC = self.canopy_cover_development(self.CC0adj, CCxAdj, CGCadj,
                                        self.CDC, tmp_tCC, 'Growth')
        self.CC[cond5221121] = tmp_CC[cond5221121]

        # No canopy growth (line 110)
        cond5221122 = (cond522112 & np.logical_not(cond5221121))
        self.CC[cond5221122] = self.CCprev[cond5221122]

        # No canopy growth (line 115)
        cond52212 = (cond5221 & np.logical_not(cond52211))
        self.CC[cond52212] = self.CCprev[cond52212]
        
        # No canopy growth (line 119)
        cond5222 = (cond522 & np.logical_not(cond5221))

        # Update CC0 if current canopy cover if less than initial canopy cover
        # size at planting
        cond52221 = (cond5222 & (self.CC < self.CC0adj))
        self.CC0adj[cond52221] = self.CC[cond52221]

        # Update actual maximum canopy cover size during growing season
        cond53 = (cond5 & (self.CC > self.CCxAct))
        self.CCxAct[cond53] = self.CC[cond53]
        
        # No more canopy growth is possible or canopy is in decline (line 132)
        cond6 = (growing_season_index & (tCCadj > self.CanopyDevEnd))

        # Mid-season stage - no canopy growth: update actual maximum canopy
        # cover size during growing season only (i.e. do not update CC)
        cond61 = (cond6 & (tCCadj < self.Senescence))
        cond611 = (cond61 & (self.CC > self.CCxAct))
        self.CCxAct[cond611] = self.CC[cond611]

        # Late season stage - canopy decline: update canopy decline coefficient
        # for difference between actual and potential CCx, and determine new
        # canopy size
        cond62 = (cond6 & np.logical_not(cond61))
        CDCadj[cond62] = (self.CDC * (self.CCxAct / self.CCx))[cond62]
        tmp_tCC = tCCadj - self.Senescence
        tmp_CC = self.canopy_cover_development(self.CC0adj, self.CCxAct, self.CGC,
                                        CDCadj, tmp_tCC, 'Decline')
        self.CC[cond62] = tmp_CC[cond62]

        # Check for crop growth termination: if the following conditions are
        # met, the crop has died
        cond63 = (cond6 & ((self.CC < 0.001) & np.logical_not(self.CropDead)))
        self.CC[cond63] = 0
        self.CropDead[cond63] = True

        # ######################################################################
        # Canopy senescence due to water stress (actual)

        cond7 = (growing_season_index & (tCCadj >= self.Emergence))

        # Check for early canopy senescence starting/continuing due to severe
        # water stress
        cond71 = (cond7 & ((tCCadj < self.Senescence) | (self.tEarlySen > 0)))

        # Early canopy senescence
        cond711 = (cond71 & (self.Ksw_Sen < 1))
        self.PrematSenes[cond711] = True
        # No prior early senescence
        cond7111 = (cond711 & (self.tEarlySen == 0))
        self.CCxEarlySen[cond7111] = self.CCprev[cond7111]

        # Increment early senescence GDD counter
        self.tEarlySen[cond711] += dtCC[cond711]

        # Adjust canopy decline coefficient for water stress
        self.water_stress(meteo, soil_water, beta = False)  # not clear why this is included again
        cond7112 = (cond711 & (self.Ksw_Sen > 0.99999))
        CDCadj[cond7112] = 0.0001
        cond7113 = (cond711 & np.logical_not(cond7112))
        CDCadj[cond7113] = ((1 - (self.Ksw_Sen ** 8)) * self.CDC)[cond7113]

        # Get new canopy cover size after senescence
        cond7114 = (cond711 & (self.CCxEarlySen < 0.001))
        CCsen[cond7114] = 0

        # Get time required to reach CC at end of previous day, given CDCadj
        cond7115 = (cond711 & np.logical_not(cond7114))
        tReq = self.canopy_cover_required_time(self.CCprev, self.CC0adj,
                                               self.CCxEarlySen, self.CGC,
                                               CDCadj, dtCC,
                                               tCCadj, 'CDC')

        # Calculate GDD's for canopy decline and determine new canopy size
        tmp_tCC = tReq + dtCC
        tmp_CCsen = self.canopy_cover_development(self.CC0adj, self.CCxEarlySen, self.CGC,
                                           CDCadj, tmp_tCC, 'Decline')
        CCsen[cond7115] = tmp_CCsen[cond7115]

        # Update canopy cover size
        cond7116 = (cond711 & (tCCadj < self.Senescence))

        # Limit CC to CCx
        CCsen[cond7116] = np.clip(CCsen, None, self.CCx)[cond7116]

        # CC cannot be greater than value on previous day
        self.CC[cond7116] = np.clip(self.CC, None, self.CCprev)[cond7116]

        # Update maximum canopy cover size during growing season
        self.CCxAct[cond7116] = self.CC[cond7116]

        # Update CC0 if current CC is less than initial canopy cover size at
        # planting
        cond71161 = (cond7116 & (self.CC < self.CC0))
        self.CC0adj[cond71161] = self.CC[cond71161]
        cond71162 = (cond7116 & np.logical_not(cond71161))
        self.CC0adj[cond71162] = self.CC0[cond71162]

        # Update CC to account for canopy cover senescence due to water stress
        cond7117 = (cond711 & np.logical_not(cond7116))
        self.CC[cond7117] = np.clip(self.CC, None, CCsen)[cond7117]

        # Check for crop growth termination
        cond7118 = (cond711
                    & ((self.CC < 0.001) & np.logical_not(self.CropDead)))
        self.CC[cond7118] = 0
        self.CropDead[cond7118] = True

        # Otherwise there is no water stress
        cond712 = (cond71 & np.logical_not(cond711))
        self.PrematSenes[cond712] = False

        # Rewatering of canopy in late season: get adjusted values of CCx and
        # CDC and update CC
        cond7121 = (cond712 & ((tCCadj > self.Senescence) & (self.tEarlySen > 0)))
        tmp_tCC = tCCadj - dtCC - self.Senescence
        tmp_CCxAdj,tmp_CDCadj = self.update_CCx_and_CDC(self.CCprev, self.CDC, self.CCx, tmp_tCC)
        CCxAdj[cond7121] = tmp_CCxAdj[cond7121]
        CDCadj[cond7121] = tmp_CDCadj[cond7121]
        tmp_tCC = tCCadj - self.Senescence
        tmp_CC = self.canopy_cover_development(self.CC0adj, CCxAdj, self.CGC,
                                        CDCadj, tmp_tCC, 'Decline')
        self.CC[cond7121] = tmp_CC[cond7121]

        # Check for crop growth termination
        cond71211 = (cond7121
                     & ((self.CC < 0.001) & np.logical_not(self.CropDead)))
        self.CC[cond71211] = 0
        self.CropDead[cond71211] = True

        # Reset early senescence counter
        self.tEarlySen[cond712] = 0

        # Adjust CCx for effects of withered canopy
        self.CCxW[cond71] = np.clip(self.CCxW, self.CC, None)[cond71]

        # ######################################################################
        # Calculate canopy size adjusted for micro-advective effects
        
        # Check to ensure potential CC is not slightly lower than actual
        cond8 = (growing_season_index & (self.CC_NS < self.CC))
        self.CC_NS[cond8] = self.CC[cond8]

        cond81 = (cond8 & (tCC < self.CanopyDevEnd))
        self.CCxAct_NS[cond81] = self.CC_NS[cond81]

        # Actual (with water stress)
        self.CCadj[growing_season_index] = (
            (1.72 * self.CC)
            - (self.CC ** 2)
            + (0.3 * (self.CC ** 3)))[growing_season_index]

        # Potential (without water stress)
        self.CCadj_NS[growing_season_index] = (
            (1.72 * self.CC_NS)
            - (self.CC_NS ** 2)
            + (0.3 * (self.CC_NS ** 3)))[growing_season_index]

        # No canopy outside growing season - set values to zero
        self.CC[np.logical_not(growing_season_index)] = 0
        self.CCadj[np.logical_not(growing_season_index)] = 0
        self.CC_NS[np.logical_not(growing_season_index)] = 0
        self.CCadj_NS[np.logical_not(growing_season_index)] = 0
        self.CCxW[np.logical_not(growing_season_index)] = 0
        self.CCxAct[np.logical_not(growing_season_index)] = 0
        self.CCxW_NS[np.logical_not(growing_season_index)] = 0
        self.CCxAct_NS[np.logical_not(growing_season_index)] = 0
    
    def harvest_index_ref_current_day(self):
        """Function to calculate reference (no adjustment for stress 
        effects) harvest index on current day
        """

        # Initialize (TODO: check it is OK to do this)
        self.HIref = np.zeros((self.nRotation, self.nLat, self.nLon))
        
        # Check if in yield formation period
        if self.CalendarType == 1:
            tAdj = self.DAP - self.DelayedCDs
        elif self.CalendarType == 2:
            tAdj = self.GDDcum - self.DelayedGDDs

        self.YieldForm = (growing_season_index & (tAdj > self.HIstart))

        # Get time for harvest index calculation
        HIt = self.DAP - self.DelayedCDs - self.HIstartCD - 1

        # Yet to reach time for HI build-up
        cond1 = (growing_season_index & (HIt <= 0))
        self.HIref[cond1] = 0
        self.PctLagPhase[cond1] = 0

        cond2 = (growing_season_index & np.logical_not(cond1))

        # HI cannot develop further as canopy is too small (no need to do
        # anything here as HIref doesn't change)
        cond21 = (cond2 & (self.CCprev <= (self.CCmin * self.CCx)))
        cond22 = (cond2 & np.logical_not(cond21))

        # If crop type is leafy vegetable or root/tuber then proceed with
        # logistic growth (i.e. no linear switch)
        cond221 = (cond22 & ((self.CropType == 1) | (self.CropType == 2)))
        self.PctLagPhase[cond221] = 100
        self.HIref[cond221] = ((self.HIini * self.HI0) / (self.HIini + (self.HI0 - self.HIini) * np.exp(-self.HIGC * HIt)))[cond221]
        # Harvest index approaching maximum limit
        cond2211 = (cond221 & (self.HIref >= (0.9799 * HI0)))
        self.HIref[cond2211] = HI0[cond2211]

        cond222 = (cond22 & (self.CropType == 3))
        # Not yet reached linear switch point, therefore proceed with logistic
        # build-up
        cond2221 = (cond222 & (HIt < self.tLinSwitch))
        self.PctLagPhase[cond2221] = (100 * (HIt / self.tLinSwitch))[cond2221]
        self.HIref[cond2221] = ((self.HIini * self.HI0) / (self.HIini + (self.HI0 - self.HIini) * np.exp(-self.HIGC * HIt)))[cond2221]
        cond2222 = (cond222 & np.logical_not(cond2221))
        self.PctLagPhase[cond2222] = 100
        # Calculate reference harvest index for current day (logistic portion)
        self.HIref[cond2222] = ((self.HIini * self.HI0) / (self.HIini + (self.HI0 - self.HIini) * np.exp(-self.HIGC * self.tLinSwitch)))[cond2222]
        # Calculate reference harvest index for current day (total = logistic + linear)
        self.HIref[cond2222] += (self.dHILinear * (HIt - self.tLinSwitch))[cond222]

        # Limit HIref and round off computed value
        cond223 = (cond22 & (self.HIref > HI0))
        self.HIref[cond223] = self.HI0[cond223]
        cond224 = (cond22 & (self.HIref <= (self.HIini + 0.004)))
        self.HIref[cond224] = 0
        cond225 = (cond22 & ((self.HI0 - self.HIref) < 0.004))
        self.HIref[cond225] = self.HI0[cond225]

        # Reference harvest index is zero outside growing season
        self.HIref[np.logical_not(growing_season_index)] = 0

    def temperature_stress(self, meteo):
        """Function to calculate temperature stress coefficients"""

        # Add rotation dimension to meteo vars
        tmin = meteo.tmin[None,:,:] * np.ones((self.nRotation))[:,None,None]
        tmax = meteo.tmax[None,:,:] * np.ones((self.nRotation))[:,None,None]
        
        # Calculate temperature stress coefficient affecting biomass growth
        KsBio_up = 1
        KsBio_lo = 0.02
        fshapeb = -1 * (np.log(((KsBio_lo * KsBio_up) - 0.98 * KsBio_lo) / (0.98 * (KsBio_up - KsBio_lo))))

        # Calculate temperature stress effects on biomass production
        cond1 = (self.BioTempStress == 0)
        self.Kst_Bio[cond1] = 1
        cond2 = (self.BioTempStress == 1)
        cond21 = (cond2 & (self.GDD >= self.GDD_up))
        self.Kst_Bio[cond21] = 1
        cond22 = (cond2 & (self.GDD <= self.GDD_lo))
        self.Kst_Bio[cond22] = 0
        cond23 = (cond2 & np.logical_not(cond21 | cond22))
        GDDrel = (self.GDD - self.GDD_lo) / (self.GDD_up - self.GDD_lo)
        self.Kst_Bio[cond23] = ((KsBio_up * KsBio_lo) / (KsBio_lo + (KsBio_up - KsBio_lo) * np.exp(-fshapeb * GDDrel)))[cond23]
        self.Kst_Bio[cond23] = (self.Kst_Bio - KsBio_lo * (1 - GDDrel))[cond23]

        # Calculate temperature stress coefficients affecting crop pollination
        KsPol_up = 1
        KsPol_lo = 0.001

        # Calculate effects of heat stress on pollination
        cond3 = (self.PolHeatStress == 0)
        self.Kst_PolH[cond3] = 1
        cond4 = (self.PolHeatStress == 1)
        cond41 = (cond4 & (tmax <= self.Tmax_lo))
        self.Kst_PolH[cond41] = 1
        cond42 = (cond4 & (tmax >= self.Tmax_up))
        self.Kst_PolH[cond42] = 0
        cond43 = (cond4 & np.logical_not(cond41 | cond42))
        Trel = (tmax - self.Tmax_lo) / (self.Tmax_up - self.Tmax_lo)
        self.Kst_PolH[cond43] = ((KsPol_up * KsPol_lo) / (KsPol_lo + (KsPol_up - KsPol_lo) * np.exp(-self.fshape_b * (1 - Trel))))[cond43]

        # Calculate effects of cold stress on pollination
        cond5 = (self.PolColdStress == 0)
        self.Kst_PolC[cond5] = 1
        cond6 = (self.PolColdStress == 1)
        cond61 = (cond6 & (tmin >= self.Tmin_up))
        self.Kst_PolC[cond61] = 1
        cond62 = (cond6 & (tmin <= self.Tmin_lo))
        self.Kst_PolC[cond62] = 0
        Trel = (self.Tmin_up - tmin) / (self.Tmin_up - self.Tmin_lo)
        self.Kst_PolC[cond62] = ((KsPol_up * KsPol_lo) / (KsPol_lo + (KsPol_up - KsPol_lo) * np.exp(-self.fshape_b * (1 - Trel))))[cond62]
        
    def biomass_accumulation(self, meteo):
        """Function to calculate biomass accumulation"""

        et0 = meteo.referencePotET[None,:,:] * np.ones((nr))[:,None,None]
        self.temperature_stress(meteo)

        # Get time for harvest index build-up
        HIt = self.DAP - self.DelayedCDs - self.HIstartCD - 1

        fswitch = np.zeros((self.nRotation, self.nLat, self.nLon))
        WPadj = np.zeros((self.nRotation, self.nLat, self.nLon))
        cond1 = (growing_season_index & (((self.CropType == 2) | (self.CropType == 3)) & (self.HIref > 0)))

        # Adjust WP for reproductive stage
        cond11 = (cond1 & (self.Determinant == 1))
        fswitch[cond11] = (self.PctLagPhase / 100)[cond11]
        cond12 = (cond1 & np.logical_not(cond11))
        cond121 = (cond12 < (self.YldFormCD / 3))
        fswitch[cond121] = (HIt / (self.YldFormCD / 3))[cond121]
        cond122 = (cond12 & np.logical_not(cond121))
        fswitch[cond122] = 1
        WPadj[cond1] = (self.WP * (1 - (1 - self.WPy / 100) * fswitch))[cond1]
        cond2 = (growing_season_index & np.logical_not(cond1))
        WPadj[cond2] = self.WP[cond2]

        # Adjust WP for CO2 effects **TODO**
        # WPadj *= self.fCO2

        # Calculate biomass accumulation on current day
        dB_NS = WPadj * (self.TrPot0 / et0) * self.Kst_Bio  # TODO: check correct TrPot is being used
        dB = WPadj * (self.TrAct / et0) * self.Kst_Bio  # TODO: check correct TrAct is being used

        # Update biomass accumulation
        self.B += dB
        self.B_NS += dB_NS

        # No biomass accumulation outside growing season
        self.B[np.logical_not(growing_season_index)] = 0
        self.B_NS[np.logical_not(growing_season_index)] = 0

    def harvest_index_adj_pre_anthesis(self):
        """Function to calculate adjustment to harvest index for 
        pre-anthesis water stress
        """
        # Calculate adjustment
        Br = self.B / self.B_NS
        Br_range = np.log(self.dHI_pre) / 5.62
        Br_upp = 1
        Br_low = 1 - Br_range
        Br_top = Br_upp - (Br_range / 3)

        # Get biomass ratio
        ratio_low = (Br - Br_low) / (Br_top - Br_low)
        ratio_upp = (Br - Br_top) / (Br_upp - Br_top)

        # Calculate adjustment factor
        self.Fpre = np.ones((self.nRotation, self.nLat, self.nLon))
        cond1 = (Br >= Br_low) & (Br < Br_top)
        self.Fpre[cond1] = (1 + (((1 + np.sin((1.5 - ratio_low) * np.pi)) / 2) * (self.dHI_pre / 100)))[cond1]
        cond2 = (((Br > Br_top) & (Br <= Br_upp)) & np.logical_not(cond1))
        self.Fpre[cond2] = (1 + (((1 + np.sin((0.5 + ratio_upp) * np.pi)) / 2) * (self.dHI_pre / 100)))[cond2]

        # No green canopy left at start of flowering so no harvestable crop
        # will develop
        self.Fpre[self.CC <= 0.01] = 0
        
    def harvest_index_adj_pollination(self, HIt):
        """Function to calculate adjustment to harvest index for 
        failure of pollination due to water or temperature stress
        """
        arr_zeros = np.zeros((self.nRotation, self.nLat, self.nLon))
        FracFlow = arr_zeros
        t1 = arr_zeros
        t2 = arr_zeros
        F1 = arr_zeros
        F2 = arr_zeros
        F = arr_zeros

        # Fractional flowering on previous day
        cond1 = (HIt > 0)
        t1[cond1] = HIt[cond1] - 1
        cond11 = (t1 > 0)
        t1pct = 100 * (t1 / self.FloweringCD)
        t1pct = np.clip(t1pct, 0, 100)
        F1[cond11] = (0.00558 * np.exp(0.63 * np.log(t1pct)) - (0.000969 * t1pct) - 0.00383)[cond11]
        F1 = np.clip(F1, 0, None)

        # Fractional flowering on current day
        t2[cond1] = HIt[cond1]
        cond12 = (t2 > 0)
        t2pct = 100 * (t2 / self.FloweringCD)
        t2pct = np.clip(t2pct, 0, 100)
        F2[cond12] = (0.00558 * np.exp(0.63 * np.log(t2pct)) - (0.000969 * t2pct) - 0.00383)[cond11]
        F2 = np.clip(F2, 0, None)

        # Weight values
        cond13 = (np.abs(F1 - F2) >= 0.0000001)
        F[cond13] = (100 * ((F1 + F2) / 2) / self.FloweringCD)[cond13]
        FracFlow[cond1] = F[cond1]
        
        # Calculate pollination adjustment for current day
        dFpol = np.zeros((self.nRotation, self.nLat, self.nLon))
        cond2 = (self.CC >= self.CCmin)
        Ks = np.minimum(self.Ksw_Pol, self.Kst_PolC, self.Kst_PolH)
        dFpol[cond2] = (Ks * FracFlow * (1 + (self.exc / 100)))[cond2]

        # Calculate pollination adjustment to date
        self.Fpol += dFpol
        self.Fpol = np.clip(self.Fpol, None, 1)

    def harvest_index_adj_post_anthesis(self):
        """Function to calculate adjustment to harvest index for 
        post-anthesis water stress
        """        
        # 1 Adjustment for leaf expansion
        tmax1 = self.CanopyDevEndCD - self.HIstartCD
        DAP = self.DAP - self.DelayedCDs
        cond1 = (
            (DAP <= (self.CanopyDevEndCD + 1))
            & (tmax1 > 0)
            & (self.Fpre > 0.99)
            & (self.CC > 0.001)
            & (self.a_HI > 0))
        dCor = (1 + (1 - self.Ksw_Exp) / self.a_HI)
        self.sCor1[cond1] += (dCor / tmax1)[cond1]
        DayCor = (DAP - 1 - self.HIstartCD)
        self.fpost_upp[cond1] = ((tmax1 / DayCor) * self.sCor1)[cond1]

        # 2 Adjustment for stomatal closure
        tmax2 = self.YldFormCD
        cond2 = (
            (DAP <= (self.HIendCD + 1))
            & (tmax2 > 0)
            & (self.Fpre > 0.99)
            & (self.CC > 0.001)
            & (self.b_HI > 0))
        dCor = ((np.exp(0.1 * np.log(self.Ksw_Sto))) * (1 - (1 - self.Ksw_Sto) / self.b_HI))
        self.sCor2[cond2] += (dCor / tmax2)[cond2]
        DayCor = (DAP - 1 - self.HIstartCD)
        self.fpost_dwn[cond2] = ((tmax2 / DayCor) * self.sCor2)[cond2]

        # Determine total multiplier
        cond3 = (tmax1 == 0) & (tmax2 == 0)
        self.Fpost[cond3] = 1
        cond4 = np.logical_not(cond3)
        cond41 = (cond4 & (tmax2 == 0))
        self.Fpost[cond41] = self.fpost_upp[cond41]
        cond42 = (cond4 & (tmax1 <= tmax2) & np.logical_not(cond41))
        self.Fpost[cond42] = (
            self.fpost_dwn
            * (((tmax1 * self.fpost_upp) + (tmax2 - tmax1)) / tmax2))[cond42]
        cond43 = (cond4 & np.logical_not(cond41 | cond42))
        self.Fpost[cond43] = (
            self.fpost_upp
            * (((tmax2 * self.fpost_dwn) + (tmax1 - tmax2)) / tmax2))[cond43]

    def harvest_index(self, meteo):
        """Function to simulate build up of harvest index"""
        
        # Calculate stresses, after updating root zone water content
        self.root_zone_water()
        self.water_stress(meteo, beta = True)

        # Calculate temperature stress
        self.temperature_stress(meteo)

        # Get reference harvest index on current day
        HIi = self.HIref
        HIadj = np.zeros((self.nRotation, self.nLat, self.nLon))

        # Get time for harvest index build up
        HIt = self.DAP - self.DelayedCDs - self.HIstartCD - 1

        # Calculate harvest index
        cond1 = (growing_season_index & self.YieldForm & (HIt > 0))

        # Root/tuber or fruit/grain crops
        cond11 = (cond1 & ((self.CropType == 2) | (self.CropType == 3)))

        # Determine adjustment for water stress before anthesis
        cond111 = (cond11 & np.logical_not(self.PreAdj))
        self.PreAdj[cond111] = True
        self.harvest_index_adj_pre_anthesis()

        # Adjustment only for fruit/grain crops
        HImax = np.zeros((self.nRotation, self.nLat, self.nLon))  # TODO: is this in the right place?
        cond112 = (cond11 & (self.CropType == 3))
        cond1121 = (cond112 & ((HIt > 0) & (HIt <= self.FloweringCD)))
        self.harvest_index_adj_pollination(HIt)
        HImax[cond112] = (self.Fpol * self.HI0)[cond112]
        cond113 = (cond11 & np.logical_not(cond112))
        HImax[cond113] = self.HI0[cond113]

        # Determine adjustments for post-anthesis water stress
        cond114 = (cond11 & (HIt > 0))
        self.harvest_index_adj_post_anthesis()

        # Limit HI to maximum allowable increase due to pre- and post-anthesis
        # water stress combinations
        HImult = np.ones((self.nRotation, self.nLat, self.nLon))
        HImult[cond11] = (self.Fpre * self.Fpost)[cond11]
        cond115 = (cond11 & (HImult > (1 + (self.dHI0 / 100))))
        HImult[cond115] = (1 + (self.dHI0 / 100))[cond115]

        # Determine harvest index on current day, adjusted for stress effects
        cond116 = (cond11 & (HImax >= HIi))
        HIadj[cond116] = (HImult * HIi)[cond116]
        cond117 = (cond11 & np.logical_not(cond116))
        HIadj[cond117] = (HImult * HImax)[cond117]

        # Leafy vegetable crops - no adjustment, harvest index equal to
        # reference value for current day
        cond12 = (cond1 & (self.CropType == 1))
        HIadj[cond12] = HIi[cond12]

        # Otherwise no build-up of harvest index if outside yield formation
        # period
        cond2 = (growing_season_index & np.logical_not(cond1))
        HIi[cond2] = self.HI[cond2]
        HIadj[cond2] = self.HIadj[cond2]

        # Store final values for current time step
        self.HI[growing_season_index] = HIi[growing_season_index]
        self.HIadj[growing_season_index] = HIadj[growing_season_index]

        # No harvestable crop outside of a growing season
        self.HI[np.logical_not(growing_season_index)] = 0
        self.HIadj[np.logical_not(growing_season_index)] = 0