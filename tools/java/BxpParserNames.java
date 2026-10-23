/*
 * BxpParserNames.java - dump the parser-store vocabulary of a BEAST jar.
 *
 * This is an evidence-collection probe used by tools/patch_xml_spec.py.
 * It talks to the *official* BEAST jar that is passed on the classpath and
 * prints tab-separated records:
 *
 *   BEAST_VERSION <version string>
 *   PARSER        <parser class>  <element name>
 *   CHILD         <element name accepted by an ElementRule>
 *
 * It never instantiates an MCMC object and never runs a chain.  Compile it
 * against each BEAST jar separately (the two distributions have different
 * parser stores):
 *
 *   javac -encoding UTF-8 -cp ".../lib/beast.jar" -d /tmp/classes BxpParserNames.java
 *   java  -cp ".../lib/beast.jar;/tmp/classes" BxpParserNames
 *
 * The source is part of BeastXPy and is distributed under the same licence as
 * the rest of the project.  BEAST itself is LGPL-2.1.
 */

import dr.app.beast.BeastParser;
import dr.app.beast.BeastVersion;
import dr.util.Version;
import dr.xml.ElementRule;
import dr.xml.XMLObjectParser;
import dr.xml.XMLSyntaxRule;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

public class BxpParserNames {

    private static void collectRules(XMLSyntaxRule rule, Set<String> names, int depth) {
        if (rule == null || depth > 6) {
            return;
        }
        if (rule instanceof ElementRule) {
            ElementRule element = (ElementRule) rule;
            String name = element.getName();
            if (name != null && name.length() > 0 && !name.equals("null")) {
                names.add(name);
            }
            XMLSyntaxRule[] nested = element.getRules();
            if (nested != null) {
                for (XMLSyntaxRule child : nested) {
                    collectRules(child, names, depth + 1);
                }
            }
        } else {
            // XMLSyntaxRule has no generic child accessor; ElementRule is the
            // only rule type that names an XML child element in BEAST 1.
            return;
        }
    }

    public static void main(String[] args) throws Exception {
        Version version = new BeastVersion();
        BeastParser parser = new BeastParser(
                new String[0], new ArrayList<String>(), false, false, true, version);
        System.out.println("BEAST_VERSION\t" + version.getVersion());

        Set<String> elements = new TreeSet<String>();
        Set<String> children = new TreeSet<String>();

        Iterator<?> parsers = parser.getParsers();
        while (parsers.hasNext()) {
            Object object = parsers.next();
            if (!(object instanceof XMLObjectParser)) {
                continue;
            }
            XMLObjectParser xmlParser = (XMLObjectParser) object;
            String className = xmlParser.getClass().getName();
            String[] parserNames = xmlParser.getParserNames();
            if (parserNames != null) {
                for (String parserName : parserNames) {
                    if (parserName != null && parserName.length() > 0) {
                        elements.add(parserName);
                        System.out.println("PARSER\t" + className + "\t" + parserName);
                    }
                }
            }
            try {
                XMLSyntaxRule[] rules = xmlParser.getSyntaxRules();
                if (rules != null) {
                    for (XMLSyntaxRule rule : rules) {
                        collectRules(rule, children, 0);
                    }
                }
            } catch (Throwable ignored) {
                // A parser whose syntax rules cannot be built is still a valid
                // parser name; it simply contributes no child evidence.
            }
        }

        for (String child : children) {
            System.out.println("CHILD\t" + child);
        }
    }
}
