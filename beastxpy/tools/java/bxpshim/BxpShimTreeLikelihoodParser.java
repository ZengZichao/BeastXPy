package bxpshim;

import dr.evolution.alignment.PatternList;
import dr.evolution.datatype.DataType;
import dr.evolution.tree.TreeUtils;
import dr.evolution.util.TaxonList;
import dr.evomodel.branchmodel.BranchModel;
import dr.evomodel.branchratemodel.BranchRateModel;
import dr.evomodel.siteratemodel.GammaSiteRateModel;
import dr.evomodel.substmodel.SubstitutionModel;
import dr.evomodel.tipstatesmodel.TipStatesModel;
import dr.evomodel.tree.TreeModel;
import dr.evomodel.treelikelihood.PartialsRescalingScheme;
import dr.inference.model.AbstractModel;
import dr.inference.model.Model;
import dr.inference.model.Parameter;
import dr.oldevomodel.substmodel.FrequencyModel;
import dr.oldevomodel.treelikelihood.TreeLikelihood;
import dr.xml.AbstractXMLObjectParser;
import dr.xml.AttributeRule;
import dr.xml.ElementRule;
import dr.xml.XMLObject;
import dr.xml.XMLParseException;
import dr.xml.XMLSyntaxRule;

/**
 * Review-harness-only parser: instantiates the *legacy pure-Java*
 * dr.oldevomodel.treelikelihood.TreeLikelihood (Java likelihood cores) while
 * reading the modern <siteModel> (GammaSiteRateModel) object produced by the
 * normal BEAST 1.10.4 parser set.
 *
 * Motivation: beagle.GeneralBeagleImpl.getSiteLogLikelihoods() is
 * unimplemented in the bundled pure-Java BEAGLE library, so every
 * BeagleTreeLikelihood.calculateLogLikelihood() call aborts on machines
 * without the native hmsbeagle library.  The legacy TreeLikelihood does not
 * depend on BEAGLE at all, so it can evaluate the same document.
 *
 * This class is NOT part of BeastXPy and is not a BEAST product; it exists
 * only to obtain a native (BEAST-1.10.4-class) instantiation + posterior
 * evaluation signal for the code review.
 */
public class BxpShimTreeLikelihoodParser extends AbstractXMLObjectParser {

    public static final String TREE_LIKELIHOOD = "treeLikelihood";
    public static final String USE_AMBIGUITIES = "useAmbiguities";
    public static final String SCALING_SCHEME = "scalingScheme";
    public static final String PARTIALS_RESTRICTION = "partialsRestriction";

    public String getParserName() {
        return TREE_LIKELIHOOD;
    }

    public Object parseXMLObject(XMLObject xo) throws XMLParseException {
        boolean useAmbiguities = xo.getAttribute(USE_AMBIGUITIES, false);
        // The shim constructs the legacy TreeLikelihood, whose constructor
        // always stores partials.  There is no XML attribute for this in the
        // official BeagleTreeLikelihoodParser rule surface either.
        boolean storePartials = true;
        boolean allowMissingTaxa = false;

        PartialsRescalingScheme scalingScheme = PartialsRescalingScheme.DEFAULT;
        if (xo.hasAttribute(SCALING_SCHEME)) {
            scalingScheme = PartialsRescalingScheme.parseFromString(
                    xo.getStringAttribute(SCALING_SCHEME));
        }

        PatternList patternList = (PatternList) xo.getChild(PatternList.class);
        TreeModel treeModel = (TreeModel) xo.getChild(TreeModel.class);
        GammaSiteRateModel siteModel = (GammaSiteRateModel) xo.getChild(GammaSiteRateModel.class);
        BranchRateModel branchRateModel = (BranchRateModel) xo.getChild(BranchRateModel.class);
        TipStatesModel tipStatesModel = (TipStatesModel) xo.getChild(TipStatesModel.class);
        dr.evomodel.substmodel.FrequencyModel rootFreqModel =
                (dr.evomodel.substmodel.FrequencyModel)
                        xo.getChild(dr.evomodel.substmodel.FrequencyModel.class);
        SubstitutionModel explicitSubst =
                (SubstitutionModel) xo.getChild(SubstitutionModel.class);
        BranchModel branchModel = (BranchModel) xo.getChild(BranchModel.class);

        if (siteModel == null) {
            throw new XMLParseException("No GammaSiteRateModel (siteModel) found for treeLikelihood");
        }

        if (xo.hasChildNamed(PARTIALS_RESTRICTION)) {
            XMLObject cxo = xo.getChild(PARTIALS_RESTRICTION);
            TaxonList taxonList = (TaxonList) cxo.getChild(TaxonList.class);
            try {
                TreeUtils.getLeavesForTaxa(treeModel, taxonList);
            } catch (TreeUtils.MissingTaxonException e) {
                throw new XMLParseException("Unable to parse taxon list: " + e.getMessage());
            }
            throw new XMLParseException("Restricting internal nodes is not yet implemented.  Contact Marc");
        }

        if (branchModel != null) {
            // Official BeagleTreeLikelihoodParser passes the BranchModel to the
            // likelihood core; the legacy TreeLikelihood has no equivalent
            // slot.  Construct the shim likelihood anyway (the model graph is
            // still exercised) but never pretend that the branch model was
            // evaluated.
            System.err.println("BXP-VALIDATE|warning|SHIM_BRANCH_MODEL|" + xo.getId()
                    + "|java-core/parse ignore <branchModel>; only <siteModel> is used");
        }

        SubstitutionModel subst = explicitSubst != null ? explicitSubst
                : siteModel.getSubstitutionModel();
        if (subst == null) {
            throw new XMLParseException("siteModel has no substitution model");
        }

        FrequencyModel oldFreq;
        if (rootFreqModel != null) {
            oldFreq = new FrequencyModel(subst.getDataType(), rootFreqModel.getFrequencies());
        } else {
            oldFreq = new FrequencyModel(
                    subst.getDataType(), subst.getFrequencyModel().getFrequencies());
        }
        OldSubstAdapter oldSubst = new OldSubstAdapter(subst, oldFreq);
        OldSiteAdapter oldSite = new OldSiteAdapter(siteModel, oldSubst, oldFreq);

        boolean forceRescaling = scalingScheme != PartialsRescalingScheme.NONE;

        return new TreeLikelihood(
                patternList,
                treeModel,
                oldSite,
                branchRateModel,
                tipStatesModel,
                useAmbiguities,
                allowMissingTaxa,
                storePartials,
                true,   // forceJavaCore: never use native likelihood cores
                forceRescaling);
    }

    public String getParserDescription() {
        return "Review shim: legacy pure-Java TreeLikelihood over a modern siteModel";
    }

    public Class getReturnType() {
        return TreeLikelihood.class;
    }

    public XMLSyntaxRule[] getSyntaxRules() {
        return rules;
    }

    /**
     * Mirror of dr.evomodelxml.treelikelihood.BeagleTreeLikelihoodParser.rules
     * (BEAST 1.10.4 and BEAST X 10.5.0).  tests/test_shim_parity.py checks
     * this equality via reflection; do not add shim-only attributes here.
     */
    private final XMLSyntaxRule[] rules = {
            AttributeRule.newBooleanRule(USE_AMBIGUITIES, true),
            new ElementRule(PatternList.class),
            new ElementRule(TreeModel.class),
            new ElementRule(GammaSiteRateModel.class),
            new ElementRule(BranchModel.class, true),
            new ElementRule(SubstitutionModel.class, true),
            new ElementRule(BranchRateModel.class, true),
            new ElementRule(TipStatesModel.class, true),
            AttributeRule.newStringRule(SCALING_SCHEME, true),
            new ElementRule(PARTIALS_RESTRICTION, new XMLSyntaxRule[] {
                    new ElementRule(TaxonList.class),
                    new ElementRule(Parameter.class),
            }, true),
            new ElementRule(TipStatesModel.class, true),
            new ElementRule(dr.evomodel.substmodel.FrequencyModel.class, true),
    };

    /**
     * Adapts the modern substitution model (which exposes getTransitionProbabilities)
     * to the legacy SubstitutionModel interface.
     */
    public static class OldSubstAdapter extends AbstractModel
            implements dr.oldevomodel.substmodel.SubstitutionModel {

        private final SubstitutionModel modern;
        private final FrequencyModel oldFreq;

        OldSubstAdapter(SubstitutionModel modern, FrequencyModel oldFreq) {
            super("bxpOldSubst");
            this.modern = modern;
            this.oldFreq = oldFreq;
        }

        public void getTransitionProbabilities(double distance, double[] matrix) {
            modern.getTransitionProbabilities(distance, matrix);
        }

        public double[][] getEigenVectors() {
            return null;
        }

        public double[][] getInverseEigenVectors() {
            return null;
        }

        public double[] getEigenValues() {
            return null;
        }

        public FrequencyModel getFrequencyModel() {
            return oldFreq;
        }

        public DataType getDataType() {
            return modern.getDataType();
        }

        protected void handleModelChangedEvent(Model model, Object object, int index) {
        }

        protected void handleVariableChangedEvent(dr.inference.model.Variable variable, int index,
                                                  dr.inference.model.Variable.ChangeType changeType) {
        }

        protected void storeState() {
        }

        protected void restoreState() {
        }

        protected void acceptState() {
        }
    }

    /**
     * Adapts the modern GammaSiteRateModel to the legacy SiteModel interface.
     */
    public static class OldSiteAdapter extends AbstractModel
            implements dr.oldevomodel.sitemodel.SiteModel {

        private final GammaSiteRateModel modern;
        private final OldSubstAdapter oldSubst;
        private final FrequencyModel oldFreq;

        OldSiteAdapter(GammaSiteRateModel modern, OldSubstAdapter oldSubst, FrequencyModel oldFreq) {
            super("bxpOldSite");
            this.modern = modern;
            this.oldSubst = oldSubst;
            this.oldFreq = oldFreq;
        }

        public int getCategoryCount() {
            return modern.getCategoryCount();
        }

        public double[] getCategoryRates() {
            return modern.getCategoryRates();
        }

        public double[] getCategoryProportions() {
            return modern.getCategoryProportions();
        }

        public double getRateForCategory(int category) {
            return modern.getRateForCategory(category);
        }

        public double getProportionForCategory(int category) {
            return modern.getProportionForCategory(category);
        }

        public dr.oldevomodel.substmodel.SubstitutionModel getSubstitutionModel() {
            return oldSubst;
        }

        public boolean integrateAcrossCategories() {
            return true;
        }

        public int getCategoryOfSite(int site) {
            throw new IllegalArgumentException("Integrating across categories");
        }

        public FrequencyModel getFrequencyModel() {
            return oldFreq;
        }

        protected void handleModelChangedEvent(Model model, Object object, int index) {
        }

        protected void handleVariableChangedEvent(dr.inference.model.Variable variable, int index,
                                                  dr.inference.model.Variable.ChangeType changeType) {
        }

        protected void storeState() {
        }

        protected void restoreState() {
        }

        protected void acceptState() {
        }
    }
}
