## Chapter 1
### 1.1 From the Ky approach to the AquaCrop model
采用经验公式来表述作物产量与蒸散发的关系，及下列公式
$$
(1-Y/Y_x)=K_y(1-ET/ET_x) \tag{1.1a}
$$
$Y$ 和 $Y_x$ 为最大和实际产量

在AquaCrop中，还采用了一下两种处理
*  将实际蒸散发 $ET$ 划分为土壤蒸发 $T$ 和作物蒸腾 $Tr$
*  最终产量 $Y$ 由生物量$B$和收获系数 $HI$ 确定
$$Y=HI*B \tag{1.1b}$$

以上第一点避免了没有用于作物生产的耗水（土壤蒸发）对结果造成的影响，第二点则使得模型可以区分很多环境条件对最终结果的影响。不同环境变化影响下面的方程，而该方程在AquaCrop作物生长模块中非常重要，
$$B=WP* \sum Tr \tag{1.1c}$$
其中 $Tr$ 是蒸腾量（mm），$WP$ 是水分生产参数（单位面积蒸发每mm水，所产生的生物量）。

方程1.1c被插入到了很多个模块中，有*the soil* 模块（水均衡），*the crop* 模块（作物生长、产量）和*atmosphere* 模块（热、降雨、蒸发需求、二氧化碳浓度等）。此外，还有一些*management* 模块（比如*irrigation, fertilization, mulches, weeds* 等）可考虑了，因为他们可以影响水均衡、作物生长，并因此影响到最终的产量。此外，AquaCrop还可以描述气候变化下的情况。

以下是AquaCrop与其他作物模型的详细的区别
* 重点关注水；
* 采用覆盖度（canopy cover）而非叶面积指数（leaf area index）；
* 水分生产率（WP）规则化于？（normalized for）大气蒸发需求和二氧化碳浓度，由此增加了模型对于考虑不同地点、季节、气候、未来气候变化的能力；
* 参数数目相对少；
* 输入仅需要明确的，且可以最直接获取的参数和变量；
* 用户界面友好；
* 在计算精度、模型复杂度和鲁棒性（简单理解，也就是耐操）间取得平衡；
* 在世界范围内的不同农业系统中均有良好的适用性。

AquaCrop模型相对简单，主要包含作物生产和对水分匮缺的相应两大部分，图示如下
![avatar](D:\Github\AquaCrop_Py\Reference Manual learning\Figs\Fig1.1b)


