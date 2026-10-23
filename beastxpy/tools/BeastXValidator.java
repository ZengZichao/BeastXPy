/*
 * BeastXValidator.java - headless Gate-3 validator for BeastXPy.
 *
 * Modes:
 *   --parse       strict XML + BEAST XMLObject parsing (no model construction)
 *   --instantiate strict parse + BeastParser.parse(reader, MCMC.class)
 *   --java-core   instantiate with BEAST's legacy pure-Java TreeLikelihood
 *                 parser instead of BeagleTreeLikelihood (no hmsbeagle needed)
 *   --beagle      native BEAGLE TreeLikelihood; fails with exit 2 when the
 *                 hmsbeagle JNI library cannot be found
 *
 * Every run emits machine-readable stages:
 *   BXP-VALIDATE|result|<stage>|<status>|<reason>|key=value|...
 * and findings:
 *   BXP-VALIDATE|<severity>|<code>|<path>|<message>
 *
 * Exit codes: 0 pass, 1 validation failure, 2 environment missing.
 */

import dr.app.beast.BeastParser;
import dr.app.beast.BeastVersion;
import dr.inference.mcmc.MCMC;
import dr.math.MathUtils;
import dr.util.Version;
import dr.xml.XMLObject;

import java.io.IOException;
import java.io.Reader;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class BeastXValidator {

    private static final String PREFIX = "BXP-VALIDATE|";

    private static void finding(String severity, String code, String path, String message) {
        System.err.println(PREFIX + severity + "|" + code + "|" + path + "|"
                + oneLine(message));
    }

    private static void stage(String name, String status, String reason, String details) {
        StringBuilder line = new StringBuilder(PREFIX)
                .append("result|").append(name).append('|')
                .append(status).append('|').append(oneLine(reason));
        if (details != null && !details.isEmpty()) {
            line.append('|').append(details);
        }
        System.out.println(line);
    }

    private static String oneLine(String text) {
        if (text == null) {
            return "";
        }
        return text.replace('\r', ' ').replace('\n', ' ').replace('|', '/');
    }

    private static void usage() {
        System.err.println(
                "Usage: BeastXValidator XML_PATH [--parse|--instantiate|--java-core|--beagle]\n"
                + "       [--seed N] [--chain-length L] [--tests N] [--beagle-threads N]\n"
                + "       [--require-beagle] [--run]\n"
                + "Exit codes: 0 = pass, 1 = invalid, 2 = environment missing,\n"
                + "            3 = input XML unavailable (validation not run)");
    }

    private static boolean is64BitJvm() {
        String arch = System.getProperty("os.arch", "").toLowerCase();
        return arch.contains("64");
    }

    private static String beagleVersion() {
        try {
            return beagle.BeagleFactory.getVersion();
        } catch (Throwable ignored) {
            return "unknown";
        }
    }

    private static boolean beagleLibraryPresent() {
        String paths = System.getProperty("java.library.path", "");
        for (String dir : paths.split(java.io.File.pathSeparator)) {
            if (dir.isEmpty()) {
                continue;
            }
            java.io.File d = new java.io.File(dir);
            String[] names = d.list();
            if (names == null) {
                continue;
            }
            for (String name : names) {
                String low = name.toLowerCase();
                if (!(low.contains("hmsbeagle") || low.contains("jhmsbeagle"))) {
                    continue;
                }
                // A 64-bit JVM cannot load hmsbeagle32.dll (and vice versa);
                // report what this runtime could actually use.
                if (low.contains("64") && !is64BitJvm()) {
                    continue;
                }
                if (low.contains("32") && is64BitJvm() && !low.contains("64")) {
                    continue;
                }
                return true;
            }
        }
        return false;
    }

    public static void main(String[] args) {
        if (args.length < 1) {
            usage();
            System.exit(2);
        }

        String xmlPath = null;
        String mode = "instantiate";
        boolean requireBeagle = false;
        boolean runChain = false;
        boolean relaxed = false;
        long seed = -1;
        Long chainLength = null;
        Long tests = null;
        Integer beagleThreads = null;

        for (int i = 0; i < args.length; i++) {
            String arg = args[i];
            if (arg.equals("--parse")) {
                mode = "parse";
            } else if (arg.equals("--instantiate") || arg.equals("--dry-parse")) {
                mode = "instantiate";
            } else if (arg.equals("--java-core")) {
                mode = "java-core";
            } else if (arg.equals("--beagle")) {
                mode = "beagle";
                requireBeagle = true;
            } else if (arg.equals("--require-beagle")) {
                requireBeagle = true;
            } else if (arg.equals("--relaxed")) {
                relaxed = true;
            } else if (arg.equals("--run")) {
                runChain = true;
            } else if (arg.equals("--seed")) {
                seed = Long.parseLong(args[++i]);
            } else if (arg.equals("--chain-length")) {
                chainLength = Long.parseLong(args[++i]);
            } else if (arg.equals("--tests")) {
                tests = Long.parseLong(args[++i]);
            } else if (arg.equals("--beagle-threads")) {
                beagleThreads = Integer.parseInt(args[++i]);
            } else if (arg.startsWith("--")) {
                finding("error", "ARGS", "<cli>", "unknown option " + arg);
                usage();
                System.exit(1);
            } else if (xmlPath == null) {
                xmlPath = arg;
            } else {
                finding("error", "ARGS", "<cli>", "exactly one XML path is allowed");
                usage();
                System.exit(1);
            }
        }

        if (xmlPath == null) {
            finding("error", "ARGS", "<cli>", "an XML path is required");
            usage();
            System.exit(2);
        }
        Path source = Paths.get(xmlPath);
        if (!Files.exists(source)) {
            finding("error", "FILE", xmlPath, "XML file not found");
            stage("input", "not-run", "XML file not found", "");
            System.exit(3);
        }

        boolean beagle = beagleLibraryPresent();
        String treeLikelihoodRuleSource =
                (mode.equals("parse") || mode.equals("java-core")) ? "shim" : "official";
        String environment = "java_version=" + System.getProperty("java.version", "?")
                + "|java_vendor=" + System.getProperty("java.vendor", "?")
                + "|java_arch=" + System.getProperty("os.arch", "?")
                + "|beagle=" + (beagle ? "native" : "unavailable")
                + "|beagle_version=" + (beagle ? beagleVersion() : "")
                + "|mode=" + mode + "|strict=" + (relaxed ? "false" : "true")
                + "|tree_likelihood_rule_source=" + treeLikelihoodRuleSource;
        if (requireBeagle && !beagle) {
            stage("environment", "environment-missing",
                    "native hmsbeagle library not found in java.library.path", environment);
            finding("error", "BEAGLE_MISSING", xmlPath,
                    "native hmsbeagle is required but was not found; install BEAGLE or use --java-core");
            System.exit(2);
        }
        stage("environment", "pass", "", environment);

        String xml;
        try {
            xml = new String(Files.readAllBytes(source), StandardCharsets.UTF_8);
        } catch (IOException ioe) {
            finding("error", "FILE", xmlPath, "cannot read XML as UTF-8: " + ioe.getMessage());
            stage("read", "not-run", ioe.getMessage(), "");
            System.exit(3);
            return;
        }

        Path temp = null;
        Path parsePath = source;
        if (chainLength != null) {
            if (chainLength <= 0) {
                finding("error", "ARGS", xmlPath, "chain-length must be positive");
                System.exit(1);
            }
            if (!xml.matches("(?s).*chainLength\\s*=\\s*\"\\d+\".*")) {
                finding("warning", "CHAIN_LENGTH", xmlPath,
                        "no chainLength attribute found; the rewritten value cannot take effect");
            }
            xml = xml.replaceFirst("chainLength\\s*=\\s*\"\\d+\"",
                    "chainLength=\"" + chainLength + "\"");
            try {
                temp = Files.createTempFile("beastxpy_validate_", ".xml");
                Files.write(temp, xml.getBytes(StandardCharsets.UTF_8));
                parsePath = temp;
            } catch (IOException ioe) {
                finding("error", "FILE", xmlPath,
                        "cannot write chain-length temp file: " + ioe.getMessage());
                System.exit(1);
            }
        }

        try {
            if (mode.equals("java-core") || mode.equals("parse")) {
                // Force the legacy pure-Java parser path.  In ``beagle`` mode
                // this property must stay unset or BEAST silently falls back
                // to GeneralBeagleImpl, whose site likelihoods are stubs.
                System.setProperty("java.only", "true");
            }
            System.setProperty("log.allow.overwrite", "true");
            if (beagleThreads != null) {
                System.setProperty("beagle.thread.count", Integer.toString(beagleThreads));
            }
            if (mode.equals("java-core")) {
                // Development-only models (e.g. the 10.5 amino-acid mixture)
                // need the official development parser set, plus the shim.
                boolean development = xml.contains("instantaneousMixtureSubstitutionModel")
                        || xml.contains("aminoAcidMixture");
                System.setProperty("parsers", development ? "devshim" : "bxpshim");
            }
            if (tests != null) {
                System.setProperty("mcmc.evaluation.count", Long.toString(tests));
                System.setProperty("mcmc.evaluation.threshold", "0.1");
            }
            if (seed >= 0) {
                MathUtils.setSeed(seed);
            }

            Version version = new BeastVersion();
            BeastParser parser = new BeastParser(
                    new String[]{parsePath.toString()}, new ArrayList<String>(),
                    false, false, !relaxed, version);

            if (mode.equals("parse")) {
                try (Reader reader = new StringReader(xml)) {
                    Map<String, XMLObject> objects = parser.parse(reader, false);
                    stage("parse", "pass", "", "objects=" + objects.size());
                }
                System.out.println(PREFIX + "info|PASS|" + xmlPath
                        + "|strict XMLObject parse passed");
                System.exit(0);
            }

            Object parsed;
            try (Reader reader = new StringReader(xml)) {
                parsed = parser.parse(reader, MCMC.class);
            }
            stage("parse", "pass", "", "");
            if (!(parsed instanceof MCMC)) {
                finding("error", "PARSE", xmlPath,
                        "document did not produce a dr.inference.mcmc.MCMC object");
                stage("instantiate", "fail", "not an MCMC object", "");
                System.exit(1);
            }
            MCMC mcmc = (MCMC) parsed;
            stage("instantiate", "pass", "",
                    "initial_score=" + mcmc.getMarkovChain().getCurrentScore()
                            + "|actual_chain_length=" + mcmc.getOptions().getChainLength());
            if (chainLength != null) {
                long seen = mcmc.getOptions().getChainLength();
                if (seen != chainLength) {
                    finding("error", "CHAIN_LENGTH", xmlPath,
                            "requested chain length " + chainLength
                                    + " but BEAST reports " + seen);
                    stage("chain-length", "fail", "observed " + seen, "chain_length=" + seen);
                    System.exit(1);
                }
                stage("chain-length", "pass", "", "chain_length=" + seen);
            }
            double score = mcmc.getMarkovChain().getCurrentScore();
            if (Double.isInfinite(score) || Double.isNaN(score)) {
                finding("error", "INITIAL_POSTERIOR", xmlPath,
                        "initial posterior is not finite (" + score + ")");
                stage("posterior", "fail", "initial score " + score, "");
                System.exit(1);
            }
            stage("posterior", "pass", "", "initial_score=" + score);

            if (runChain || tests != null) {
                mcmc.run();
                stage("chain", "pass", "", "final_score="
                        + mcmc.getMarkovChain().getCurrentScore());
            }
            System.out.println(PREFIX + "info|PASS|" + xmlPath
                    + "|validated by BEAST " + version.getVersionString()
                    + " (" + mode + ")");
            System.exit(0);
        } catch (dr.xml.XMLParseException pxe) {
            finding("error", "XML_PARSE", xmlPath,
                    "BEAST rejected the document: " + pxe.getMessage());
            if (mode.equals("parse")) {
                stage("parse", "fail", pxe.getMessage(), "");
            } else {
                stage("instantiate", "fail", pxe.getMessage(), "");
            }
            System.exit(1);
        } catch (Throwable ex) {
            String message = ex.getMessage() == null ? ex.toString() : ex.getMessage();
            String code = "INSTANTIATION";
            if (message.contains("initial posterior is zero")) {
                code = "INITIAL_POSTERIOR";
            } else if (message.contains("getSiteLogLikelihoods not implemented")) {
                code = "BEAGLE_INCOMPLETE";
            }
            finding("error", code, xmlPath, message);
            stage(mode.equals("parse") ? "parse" : "instantiate", "fail", message, "");
            for (StackTraceElement element : ex.getStackTrace()) {
                System.err.println("    at " + element);
            }
            System.exit(1);
        } finally {
            if (temp != null) {
                try {
                    Files.deleteIfExists(temp);
                } catch (IOException ignored) {
                    // best effort cleanup
                }
            }
        }
    }
}
